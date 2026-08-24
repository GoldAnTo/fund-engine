import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchOsRoutes } from "../../app/routes";
import * as researchFoundation from "./researchFoundation";

const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = { project: uuid(1), company: uuid(2), securityA: uuid(3), securityB: uuid(4), industry: uuid(5), companyVersion: uuid(6), securityVersionA: uuid(7), securityVersionB: uuid(8), industryVersion: uuid(9), draft: uuid(10), mandate: uuid(11), scope: uuid(12), agenda: uuid(13), basis: uuid(14), priceA: uuid(15), priceB: uuid(16), capital: uuid(17), rightsA: uuid(18), rightsB: uuid(19), membershipA: uuid(20), membershipB: uuid(21) };
const hash = "a".repeat(64);
const now = "2026-08-24T00:00:00Z";
const dto = { schema_version: "underwriting.v1" } as const;

function json(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-request-id": "req-setup" } });
}

function objectItems(): object[] {
  return [
    { ...dto, object_id: ids.company, identity_version_id: ids.companyVersion, kind: "company", external_key: "CN:300750:COMPANY", canonical_name: "宁德时代新能源科技股份有限公司", symbol: null, exchange: null, share_class: null, trading_currency: null },
    { ...dto, object_id: ids.securityA, identity_version_id: ids.securityVersionA, kind: "security", external_key: "SZSE:300750", canonical_name: "宁德时代 A 股", symbol: "300750", exchange: "SZSE", share_class: "A", trading_currency: "CNY" },
    { ...dto, object_id: ids.securityB, identity_version_id: ids.securityVersionB, kind: "security", external_key: "HKEX:03750", canonical_name: "宁德时代 H 股", symbol: "03750", exchange: "HKEX", share_class: "H", trading_currency: "CNY" },
    { ...dto, object_id: ids.industry, identity_version_id: ids.industryVersion, kind: "industry", external_key: "INDUSTRY:BATTERY", canonical_name: "动力电池", symbol: null, exchange: null, share_class: null, trading_currency: null },
  ];
}
const searchBody = (items = objectItems()) => ({ ...dto, items });
const projectBody = (targets = [ids.securityB, ids.securityA]) => ({ ...dto, id: ids.project, primary_company_id: ids.company, target_security_ids: targets, company_identity: { ...dto, object_id: ids.company, identity_version_id: ids.companyVersion, canonical_name: "宁德时代新能源科技股份有限公司" }, security_identities: targets.map((objectId) => { const item = objectItems().find((candidate) => Reflect.get(candidate, "object_id") === objectId) as Record<string, unknown>; return { ...dto, object_id: objectId, identity_version_id: item.identity_version_id, canonical_name: item.canonical_name, symbol: item.symbol, exchange: item.exchange, share_class: item.share_class, trading_currency: item.trading_currency }; }), content_hash: hash, created_at: now });
function draftBody(complete = false) {
  return { ...dto, id: ids.draft, project_id: ids.project, base_revision_id: null, lock_version: complete ? 2 : 1, content: { ...dto, publication_status: "draft", mandate_id: complete ? ids.mandate : null, scope_id: complete ? ids.scope : null, agenda_id: complete ? ids.agenda : null, historical_basis_id: complete ? ids.basis : null, price_snapshot_ids: complete ? [ids.priceB, ids.priceA] : [], fx_snapshot_ids: [], capital_structure_snapshot_id: complete ? ids.capital : null, security_rights_ids: complete ? [ids.rightsB, ids.rightsA] : [], user_focus: null }, created_at: now, updated_at: now };
}
const mandateBody = () => ({ ...dto, id: ids.mandate, project_id: ids.project, mandate_key: `product.project:${ids.project}`, horizon_years: 3, base_currency: "CNY", required_return: "0.12", permanent_loss_limit: "0.25", comparison_set: ["全球动力电池企业"], benchmark_key: null, required_excess_return: null, effective_at: now, expires_at: null, version: 1, supersedes_id: null, content_hash: hash, created_at: now });
const scopeBody = () => ({ ...dto, id: ids.scope, project_id: ids.project, version: 1, payload: { ...dto, primary_company_id: ids.company, target_security_ids: [ids.securityB, ids.securityA], industry_ids: [], covered_segments: [], user_focus: null, exclusions: [] }, supersedes_id: null, content_hash: hash, created_at: now });
const basisBody = () => ({ ...dto, id: ids.basis, cutoff_at: now, price_as_of: null, source_manifest_hash: hash, definition_bundle_hash: hash, parser_bundle_hash: hash, boundary_schema_version: "product.historical-basis.v1", content_hash: hash, created_at: now });

function previewBody() {
  const boundary = { ...dto, historical_basis_id: ids.basis, mandate_id: ids.mandate, scope_id: ids.scope, agenda_id: ids.agenda, price_snapshot_ids: [ids.priceA, ids.priceB], fx_snapshot_ids: [], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsA, ids.rightsB], parent_revision_id: null };
  return { ...dto, project_id: ids.project, expected_lock_version: 2, boundary_as_of: now, assessment: { ...dto, answerability: "not_answerable", direction: null, confidence: null, publication_status: "user_frozen", blockers: ["missing_key_baseline"], resolution_requirements: ["补齐关键基线证据"], next_review_at: null, parent_assessment_id: null, content_hash: hash }, boundary, boundary_hash: hash, manifest: { schema_version: "underwriting.research-revision-manifest.v1", project_id: ids.project, project_ref: { project_id: ids.project, content_hash: hash }, project_membership_refs: [{ membership_id: ids.membershipA, security_id: ids.securityA, content_hash: hash }, { membership_id: ids.membershipB, security_id: ids.securityB, content_hash: hash }], primary_object_id: ids.company, boundary_ref: "$boundary", mandate_id: ids.mandate, scope_id: ids.scope, agenda_id: ids.agenda, historical_basis_id: ids.basis, price_snapshot_ids: [ids.priceB, ids.priceA], fx_snapshot_ids: [], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsB, ids.rightsA], market_snapshot_refs: [`security_rights:${ids.rightsB}`, `price:${ids.priceA}`, `capital_structure:${ids.capital}`, `price:${ids.priceB}`, `security_rights:${ids.rightsA}`], model_refs: [], assessment_ref: "$assessment", memo_ref: null, parent_revision_id: null }, manifest_hash: hash };
}

function parseBody(init?: RequestInit): Record<string, unknown> {
  if (typeof init?.body !== "string") throw new Error("missing JSON body");
  const value: unknown = JSON.parse(init.body);
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("invalid JSON body");
  return value as Record<string, unknown>;
}

type Captured = { url: string; method: string; body: Record<string, unknown> | null };
function server(options: { draftFailures?: number; scopeFailures?: number; mandateFailures?: number; searchItems?: object[]; existingRights?: boolean } = {}) {
  const requests: Captured[] = [];
  let draftFailures = options.draftFailures ?? 0;
  let scopeFailures = options.scopeFailures ?? 0;
  let mandateFailures = options.mandateFailures ?? 0;
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = method === "GET" ? null : parseBody(init);
    requests.push({ url, method, body });
    if (url.includes("/product/objects?")) return json(searchBody(options.searchItems ?? objectItems()));
    if (url.endsWith("/product/projects") && method === "POST") return json(projectBody(body?.target_security_ids as string[] | undefined), 201);
    if (url.endsWith(`/projects/${ids.project}`) && method === "GET") return json(projectBody());
    if (url.endsWith("/draft") && method === "GET") {
      if (draftFailures-- > 0) return json({ ...dto, error: { code: "temporary", message: "草稿读取失败", request_id: "req-setup", details: null } }, 503);
      return json(draftBody());
    }
    if (url.endsWith("/mandates")) {
      if (mandateFailures-- > 0) return json({ ...dto, error: { code: "validation_failed", message: "必要回报率超出允许范围", request_id: "req-setup", details: null } }, 422);
      return json({
        ...mandateBody(),
        horizon_years: body?.horizon_years,
        base_currency: body?.base_currency,
        required_return: body?.required_return,
        permanent_loss_limit: body?.permanent_loss_limit,
        comparison_set: body?.comparison_set,
        benchmark_key: body?.benchmark_key,
        required_excess_return: body?.required_excess_return,
        effective_at: body?.effective_at,
        expires_at: body?.expires_at,
      }, 201);
    }
    if (url.endsWith("/scopes")) {
      if (scopeFailures-- > 0) return json({ ...dto, error: { code: "temporary", message: "范围保存失败", request_id: "req-setup", details: null } }, 503);
      return json(scopeBody(), 201);
    }
    if (url.endsWith("/agendas") && body) return json({ ...dto, id: ids.agenda, project_id: ids.project, version: 1, scope_id: ids.scope, payload: { ...dto, items: body.items }, generator_provenance: body.generator, supersedes_id: null, content_hash: hash, created_at: now }, 201);
    if (url.endsWith("/historical-bases")) return json(basisBody(), 201);
    if (url.endsWith("/price-snapshots") && body) {
      const securityId = String(body.security_identity_id);
      return json({ ...dto, id: securityId === ids.securityA ? ids.priceA : ids.priceB, security_identity_id: securityId, price: body.price, currency: body.currency, price_type: body.price_type, adjustment_basis: body.adjustment_basis, market_at: body.market_at, available_at: body.available_at, source_id: body.source_id, raw_hash: body.raw_hash, content_hash: hash, created_at: now }, 201);
    }
    if (url.endsWith("/fx-snapshots") && body) return json({ ...dto, id: uuid(22), ...body, content_hash: hash, created_at: now }, 201);
    if (url.endsWith("/capital-structure-snapshots") && body) return json({ ...dto, id: ids.capital, company_id: ids.company, currency: body.currency, cash: body.cash, debt: body.debt, minority_interest: body.minority_interest, investments: body.investments, pension_liabilities: body.pension_liabilities, other_adjustments: body.other_adjustments, basic_shares: body.basic_shares, diluted_shares: body.diluted_shares, potential_dilution_descriptors: body.potential_dilution_descriptors, report_period_start: body.report_period_start, report_period_end: body.report_period_end, market_at: body.market_at, available_at: body.available_at, source_id: body.source_id, raw_hash: body.raw_hash, content_hash: hash, created_at: now }, 201);
    if (url.includes("/security-rights/effective?") && method === "GET") {
      const parsed = new URL(url, "http://test"); const securityId = parsed.searchParams.get("security_identity_id") ?? ""; const asOf = parsed.searchParams.get("as_of") ?? now;
      const effective = options.existingRights ? { ...dto, id: securityId === ids.securityA ? ids.rightsA : ids.rightsB, security_identity_id: securityId, version: 1, economic_units: "1", votes_per_unit: "1", conversion_ratio: "1", adr_ratio: "1", dividend_rights_per_unit: "1", effective_from: now, effective_to: null, source_id: "listing-rules", raw_hash: hash, supersedes_id: null, content_hash: hash, created_at: now } : null;
      return json({ ...dto, security_identity_id: securityId, as_of: asOf, effective, head_id: effective?.id ?? null });
    }
    if (url.endsWith("/security-rights") && body) {
      const securityId = String(body.security_identity_id);
      return json({ ...dto, id: securityId === ids.securityA ? ids.rightsA : ids.rightsB, security_identity_id: securityId, version: 1, economic_units: body.economic_units, votes_per_unit: body.votes_per_unit, conversion_ratio: body.conversion_ratio, adr_ratio: body.adr_ratio, dividend_rights_per_unit: body.dividend_rights_per_unit, effective_from: body.effective_from, effective_to: body.effective_to, source_id: body.source_id, raw_hash: body.raw_hash, supersedes_id: null, content_hash: hash, created_at: now }, 201);
    }
    if (url.endsWith("/draft") && method === "PATCH") return json(draftBody(true));
    if (url.endsWith("/publication-preview")) return json(previewBody());
    throw new Error(`unexpected request: ${method} ${url}`);
  });
  return { fetch, requests };
}

function renderPage() {
  render(<MemoryRouter initialEntries={["/research/new"]}><ResearchOsRoutes /></MemoryRouter>);
}
async function choose(user: ReturnType<typeof userEvent.setup>, both = true) {
  await screen.findByRole("heading", { name: "建立研究项目" });
  await user.type(screen.getByLabelText("搜索公司、证券或行业"), "CATL");
  await user.click(screen.getByRole("button", { name: "搜索对象" }));
  await screen.findByRole("region", { name: "对象搜索结果" });
  await user.click(screen.getByRole("radio", { name: /Company.*宁德时代新能源/ }));
  await user.click(screen.getByRole("checkbox", { name: /Security.*300750.*SZSE/ }));
  if (both) await user.click(screen.getByRole("checkbox", { name: /Security.*03750.*HKEX/ }));
}
async function fill(user: ReturnType<typeof userEvent.setup>) {
  const values: Array<[string, string]> = [["研究期限（年）", "3"], ["必要回报率", "0.12"], ["永久损失上限", "0.25"], ["比较集合", "全球动力电池企业"], ["生效时间", "2026-08-24T08:00"], ["历史截止时间", "2026-08-24T08:00"], ["来源清单哈希", hash], ["定义包哈希", hash], ["解析器包哈希", hash], ["现金", "100"], ["债务", "40"], ["少数股东权益", "5"], ["投资资产", "10"], ["养老金负债", "0"], ["其他调整", "0"], ["基本股数", "4400"], ["稀释股数", "4410"], ["报告期开始", "2026-01-01T00:00"], ["报告期结束", "2026-06-30T23:59"], ["资本结构市场时间", "2026-08-24T08:00"], ["资本结构可用时间", "2026-08-24T08:05"], ["资本结构来源", "filing"], ["资本结构原文哈希", hash]];
  for (const symbol of ["300750", "03750"]) values.push([`${symbol} 价格`, "210.5"], [`${symbol} 市场时间`, "2026-08-24T08:00"], [`${symbol} 可用时间`, "2026-08-24T08:05"], [`${symbol} 价格来源`, "exchange"], [`${symbol} 价格原文哈希`, hash], [`${symbol} 经济单位`, "1"], [`${symbol} 每单位投票权`, "1"], [`${symbol} 转换比例`, "1"], [`${symbol} ADR 比例`, "1"], [`${symbol} 每单位分红权`, "1"], [`${symbol} 权利生效时间`, "2026-08-24T08:00"], [`${symbol} 权利来源`, "filing"], [`${symbol} 权利原文哈希`, hash]);
  for (const [label, value] of values) { const control = screen.getByLabelText(label); await user.clear(control); await user.type(control, value); }
}

describe("new independent investment research setup", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("distinguishes identities and reports zero results", async () => {
    const user = userEvent.setup();
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(json(searchBody([]))).mockResolvedValueOnce(json(searchBody()));
    vi.stubGlobal("fetch", fetch); renderPage();
    await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "missing"); await user.click(screen.getByRole("button", { name: "搜索对象" }));
    expect(await screen.findByText(/没有找到匹配/)).toBeVisible();
    await user.clear(screen.getByLabelText("搜索公司、证券或行业")); await user.type(screen.getByLabelText("搜索公司、证券或行业"), "CATL"); await user.click(screen.getByRole("button", { name: "搜索对象" }));
    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(within(results).getAllByText("Security")).toHaveLength(2); expect(within(results).getByText("Industry")).toBeVisible();
  });

  it("keeps an unrelated Security 422 inline", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(json(searchBody())).mockResolvedValueOnce(json({ ...dto, error: { code: "validation_failed", message: "所选证券不属于该公司", request_id: "req-setup", details: {} } }, 422)));
    renderPage(); await choose(user, false); await user.click(screen.getByRole("button", { name: "提交身份账本校验" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("所选证券不属于该公司"); expect(screen.queryByText(/关系已确认/)).not.toBeInTheDocument();
  });

  it("retries draft reading without creating the project again", async () => {
    const user = userEvent.setup(); const product = server({ draftFailures: 1 }); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await user.click(await screen.findByRole("button", { name: "重试读取草稿" }));
    expect(await screen.findByRole("heading", { name: /研究任务与边界/ })).toBeVisible();
    expect(product.requests.filter((r) => r.url.endsWith("/product/projects") && r.method === "POST")).toHaveLength(1); expect(product.requests.filter((r) => r.url.endsWith("/draft") && r.method === "GET")).toHaveLength(2);
  });

  it("validates required fields before generating or submitting an agenda", async () => {
    const user = userEvent.setup(); const product = server(); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ });
    const firstRequired = screen.getByLabelText("研究期限（年）");
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请先完成所有必填字段");
    expect(firstRequired).toHaveFocus();
    expect(screen.queryByRole("region", { name: "模板议程预览" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立版本边界并进入工作台" })).toBeDisabled();
    expect(product.requests.some((request) => request.url.endsWith("/agendas") || request.url.endsWith("/mandates"))).toBe(false);
  });

  it("uses Security object IDs, optional scope, deterministic agenda, timezone, and resumes missing work", async () => {
    const user = userEvent.setup(); const product = server({ scopeFailures: 1 }); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    expect(screen.getByLabelText("研究焦点（可选）")).not.toBeRequired(); expect(screen.getByLabelText("冻结时区 / UTC offset")).toHaveValue("+08:00");
    await user.click(screen.getByRole("button", { name: "预览模板议程" })); expect(await screen.findByRole("region", { name: "模板议程预览" })).toHaveTextContent("不构成研究完成或投资结论");
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" })); expect(await screen.findByRole("alert")).toHaveTextContent("范围保存失败");
    expect(screen.getByLabelText("研究期限（年）")).toBeDisabled();
    const mandateCount = product.requests.filter((r) => r.url.endsWith("/mandates")).length; await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" })); await screen.findByRole("heading", { name: "研究工作台" });
    expect(product.requests.filter((r) => r.url.endsWith("/mandates"))).toHaveLength(mandateCount);
    const prices = product.requests.filter((r) => r.url.endsWith("/price-snapshots")).map((r) => r.body?.security_identity_id).sort(); const rights = product.requests.filter((r) => r.url.endsWith("/security-rights")).map((r) => r.body?.security_identity_id).sort();
    expect(prices).toEqual([ids.securityA, ids.securityB].sort()); expect(rights).toEqual([ids.securityA, ids.securityB].sort()); expect(prices).not.toContain(ids.securityVersionA);
    expect(product.requests.find((r) => r.url.endsWith("/scopes"))?.body).toMatchObject({ covered_segments: [], exclusions: [], user_focus: null });
    expect(product.requests.find((r) => r.url.endsWith("/agendas"))?.body).toMatchObject({ generator: { method: "deterministic_template", template_key: "product.foundation.agenda", template_version: "1.0.0", input_summary_hash: expect.stringMatching(/^[0-9a-f]{64}$/) } });
    expect(product.requests.find((r) => r.url.endsWith("/price-snapshots"))?.body?.market_at).toBe("2026-08-24T00:00:00.000Z");
  });

  it("quotes FX from each Security currency into the mandate base currency", async () => {
    const user = userEvent.setup();
    const items = objectItems();
    Object.assign(items[2], { trading_currency: "USD" });
    const product = server({ searchItems: items, scopeFailures: 1 });
    vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" }));
    await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    for (const [label, value] of [["USD/CNY 汇率", "7.2"], ["USD/CNY 市场时间", "2026-08-24T08:00"], ["USD/CNY 可用时间", "2026-08-24T08:05"], ["USD/CNY 来源", "central-bank"], ["USD/CNY 原文哈希", hash]] as const) {
      const control = screen.getByLabelText(label); await user.clear(control); await user.type(control, value);
    }
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("范围保存失败");
    expect(product.requests.find((request) => request.url.endsWith("/fx-snapshots"))?.body).toMatchObject({
      base_currency: "USD", quote_currency: "CNY", quote_direction: "quote_per_base", rate: "7.2",
    });
  });

  it("keeps a failed mandate editable and retries only that failed step", async () => {
    const user = userEvent.setup(); const product = server({ mandateFailures: 1 }); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("必要回报率超出允许范围");
    expect(screen.getByLabelText("必要回报率")).toBeEnabled();
    const counts = Object.fromEntries(["/scopes", "/historical-bases", "/price-snapshots", "/capital-structure-snapshots", "/security-rights"].map((suffix) => [suffix, product.requests.filter((request) => request.url.endsWith(suffix)).length]));
    await user.clear(screen.getByLabelText("必要回报率")); await user.type(screen.getByLabelText("必要回报率"), "0.15");
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" }));
    await screen.findByRole("heading", { name: "研究工作台" });
    for (const [suffix, count] of Object.entries(counts)) expect(product.requests.filter((request) => request.url.endsWith(suffix))).toHaveLength(count);
    expect(product.requests.filter((request) => request.url.endsWith("/mandates"))).toHaveLength(2);
  });

  it("performs no writes when a Security lacks trading currency", async () => {
    const user = userEvent.setup(); const items = objectItems(); Object.assign(items[1], { trading_currency: null });
    const product = server({ searchItems: items }); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("缺少交易货币身份");
    expect(product.requests.filter((request) => request.method !== "GET")).toHaveLength(1);
  });

  it("reuses distinct effective rights for each Security without appending new versions", async () => {
    const user = userEvent.setup(); const product = server({ existingRights: true }); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" }));
    await screen.findByRole("heading", { name: "研究工作台" });
    expect(product.requests.filter((request) => request.url.includes("/security-rights/effective?"))).toHaveLength(2);
    expect(product.requests.filter((request) => request.url.endsWith("/security-rights") && request.method === "POST")).toHaveLength(0);
    expect(product.requests.find((request) => request.url.endsWith("/draft") && request.method === "PATCH")?.body?.security_rights_ids).toEqual([ids.rightsA, ids.rightsB]);
  });

  it("does not restore a stale agenda preview after its inputs change", async () => {
    const user = userEvent.setup(); const product = server(); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    let resolveAgenda!: (value: researchFoundation.GeneratedFoundationAgenda) => void;
    vi.spyOn(researchFoundation, "generateFoundationAgenda").mockImplementationOnce(() => new Promise((resolve) => { resolveAgenda = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "预览模板议程" }));
    expect(researchFoundation.generateFoundationAgenda).toHaveBeenCalledTimes(1);
    fireEvent.input(screen.getByLabelText("研究焦点（可选）"), { target: { value: "新的焦点" } });
    await act(async () => resolveAgenda({
      items: ["旧议程"],
      inputSummaryHash: hash,
      generator: { schema_version: "underwriting.v1", method: "deterministic_template", template_key: "product.foundation.agenda", template_version: "1.0.0", model_name: null, prompt_template_version: null, input_summary_hash: hash, output_hash: hash },
    }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "模板议程预览" })).not.toBeInTheDocument());
  });

  it("uses a synchronous submission lock to prevent duplicate writes", async () => {
    const user = userEvent.setup(); const product = server({ scopeFailures: 2 }); vi.stubGlobal("fetch", product.fetch); renderPage();
    await choose(user); await user.click(screen.getByRole("button", { name: "提交身份账本校验" })); await screen.findByRole("heading", { name: /研究任务与边界/ }); await fill(user);
    await user.click(screen.getByRole("button", { name: "预览模板议程" }));
    const form = screen.getByRole("button", { name: "建立版本边界并进入工作台" }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(await screen.findByRole("alert")).toHaveTextContent("范围保存失败");
    expect(product.requests.filter((request) => request.url.endsWith("/mandates"))).toHaveLength(1);
  });
});
