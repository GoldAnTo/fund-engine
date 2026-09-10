#!/usr/bin/env node
/**
 * Exercise the normal browser application against an isolated live API.
 *
 * This deliberately avoids Vite mock mode and FastAPI TestClient. It proves
 * the compiled frontend's default HTTP adapter carries the bearer token while
 * creating a Case and then reading that Case from the event desk.
 */
import { execFileSync, spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(frontend, "..");
const backend = path.join(root, "backend");
const python = process.env.PYTHON || "python";
const token = "live-ui-verifier-token";
const tenant = "live-ui-verifier-team";

function freePort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function start(command, args, options) {
  const stdout = [];
  const stderr = [];
  const process = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
  process.stdout.on("data", (chunk) => stdout.push(String(chunk)));
  process.stderr.on("data", (chunk) => stderr.push(String(chunk)));
  return { process, stdout, stderr };
}

async function stop(server) {
  if (!server || server.process.exitCode !== null) return;
  server.process.kill("SIGTERM");
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      server.process.kill("SIGKILL");
      resolve();
    }, 5000);
    server.process.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function waitFor(url, { headers = {}, label }) {
  let latest = "";
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(url, { headers });
      latest = `${response.status} ${await response.text()}`;
      if (response.ok) return;
    } catch (error) {
      latest = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`${label} did not become ready: ${latest}`);
}

async function apiJson(base, path, token, init = {}) {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.headers || {}),
    },
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`protocol setup ${init.method || "GET"} ${path} failed (${response.status}): ${body}`);
  }
  return body ? JSON.parse(body) : null;
}

async function prepareReadyProtocol({ apiBase, token, caseId }) {
  // A fresh verifier database deliberately has no governed metric catalogue.
  // Seed the minimal reviewed protocol through the same public governance API,
  // then leave the monitored run itself to the normal browser UI below.
  const monitor = await apiJson(apiBase, `/research-cases/${caseId}/monitor`, token);
  const thesisId = monitor.monitor?.factor_ids[0];
  if (!thesisId) throw new Error("created Case did not expose a confirmed factor for its monitor");
  const documents = await apiJson(apiBase, `/event-research/${caseId}/documents`, token);
  const baseline = documents.items.find(
    (document) => document.source_contract?.status === "admitted" && document.source_contract.permissions?.display,
  );
  if (!baseline) throw new Error("created Case did not retain an admitted frozen baseline document");
  const metric = await apiJson(apiBase, "/metric-definitions", token, {
    method: "POST",
    body: JSON.stringify({
      metric_id: "live_verifier_company_revenue",
      display_name: "验收公司收入",
      canonical_definition: "隔离验收用的公司收入结果指标",
      entity_scope: "company",
      unit: "yuan",
      frequency: "quarterly",
      period_semantics: "period_end",
      allowed_source_roles: ["primary_disclosure"],
      role_eligibility: ["outcome", "driver"],
      approved_by: "human:data-governance",
      reason: "真实浏览器监控验收",
    }),
  });
  const binding = await apiJson(apiBase, `/theses/${thesisId}/outcome-bindings`, token, {
    method: "POST",
    body: JSON.stringify({
      metric_definition_id: metric.id,
      entity_scope: { company_id: "company:live-verifier", company: "live-verifier" },
      direction: "increase",
      baseline: {
        source_ref: `document:${baseline.id}`,
        value: "1",
        unit: "yuan",
        observed_period: "2026-01-01",
        available_at: baseline.available_at,
      },
      horizon_start: "2026-01-02",
      horizon_end: "2026-12-31",
      reviewer: "human:researcher",
      reason: "冻结本次运行的可回放结果基线",
    }),
  });
  await apiJson(apiBase, `/outcome-bindings/${binding.id}/approve`, token, {
    method: "POST",
    body: JSON.stringify({ reviewer: "human:reviewer", reason: "验收协议审核" }),
  });
  const templates = await apiJson(apiBase, "/mechanism-templates", token);
  const template = templates.find((item) => item.template_key === "overseas_ai_capex_to_china_hardware");
  if (!template) throw new Error("reviewed mechanism template was not available");
  await apiJson(apiBase, `/research-cases/${caseId}/mechanism-selection`, token, {
    method: "POST",
    body: JSON.stringify({
      template_version_id: template.id,
      reviewer: "human:researcher",
      reason: "真实浏览器验收选择可检验路径",
    }),
  });
  await Promise.all(template.edges.map((edge) => apiJson(
    apiBase,
    `/research-cases/${caseId}/mechanism-edges/${edge.id}/verification-rules`,
    token,
    {
      method: "POST",
      body: JSON.stringify({
        metric_definition_id: metric.id,
        expected_direction: "increase",
        support_predicate: "公司一手披露同口径指标增长",
        contradiction_predicate: "公司一手披露同口径指标下降",
        allowed_source_roles: ["primary_disclosure"],
        observed_period_start: "2026-01-01",
        observed_period_end: "2026-12-31",
        available_at_deadline: "2027-03-31",
        next_verification_event: "下一次公司财报披露",
        reviewer: "human:researcher",
        reason: `验收机制边 ${edge.edge_key}`,
      }),
    },
  )));
  const gate = await apiJson(apiBase, `/theses/${thesisId}/researchability`, token);
  if (gate.status !== "ready") {
    throw new Error(`governed protocol did not become ready: ${JSON.stringify(gate)}`);
  }
  return { thesisId };
}

async function prepareReviewedSourceStatement({ apiBase, token, caseId }) {
  // A market claim may only be registered from a reviewed SourceStatement.
  // This setup creates that statement through the visible-source review API;
  // the subsequent ReportClaim and KeyFactor are created by browser actions.
  const documents = await apiJson(apiBase, `/event-research/${caseId}/documents`, token);
  const baseline = documents.items.find(
    (document) => document.source_contract?.status === "admitted" && document.source_contract.permissions?.display,
  );
  if (!baseline) throw new Error("created Case did not retain a displayable document for market registration");
  const detail = await apiJson(apiBase, `/documents/${baseline.id}?research_mode=true`, token);
  const span = detail.spans.find((item) => item.verbatim_text?.trim());
  if (!span) throw new Error("created Case did not retain a source span for market registration");
  const candidate = await apiJson(apiBase, `/research-cases/${caseId}/atomic-claims`, token, {
    method: "POST",
    body: JSON.stringify({
      source_span_id: span.id,
      normalized_text: span.verbatim_text,
      claim_type: "research_opinion",
      assertion_actor: "human:researcher",
      actor: "human:researcher",
    }),
  });
  const review = await apiJson(apiBase, `/atomic-claims/${candidate.id}/reviews`, token, {
    method: "POST",
    body: JSON.stringify({
      outcome: "confirmed",
      reviewer: "human:reviewer",
      reason: "验收时已在冻结原文中逐句核对。",
      idempotency_key: `live-market-source-${caseId}`,
    }),
  });
  if (!review.published_source_statement?.id) {
    throw new Error(`atomic source review did not publish a SourceStatement: ${JSON.stringify(review)}`);
  }
  return {
    documentVersionId: baseline.id,
    sourceSpanId: span.id,
    sourceStatementId: review.published_source_statement.id,
  };
}

async function prepareReviewedActualSourceStatement({ apiBase, token, caseId }) {
  // The forecast UI may only record a later actual against another admitted,
  // reviewed frozen source.  This setup deliberately uses the normal Case
  // material endpoint rather than inserting a document into the database.
  const attached = await apiJson(apiBase, `/event-research/${caseId}/materials`, token, {
    method: "POST",
    body: JSON.stringify({
      raw_input: "公司 2026 年度公告：验收公司 2026 年归母净利润为 1.1 亿元。",
      source_type: "pasted_snapshot",
      source_metadata: {
        file_name: "验收公司 2026 年度公告",
        permissions: { ai_processing: true, display: true, export: false, api: false },
      },
      actor: "human:researcher",
    }),
  });
  const detail = await apiJson(apiBase, `/documents/${attached.document_version_id}?research_mode=true`, token);
  const span = detail.spans.find((item) => item.verbatim_text?.trim());
  if (!span) throw new Error("attached actual-source document did not retain a source span");
  const candidate = await apiJson(apiBase, `/research-cases/${caseId}/atomic-claims`, token, {
    method: "POST",
    body: JSON.stringify({
      source_span_id: span.id,
      normalized_text: span.verbatim_text,
      claim_type: "reported_claim",
      assertion_actor: "company:live-verifier",
      actor: "human:researcher",
    }),
  });
  const review = await apiJson(apiBase, `/atomic-claims/${candidate.id}/reviews`, token, {
    method: "POST",
    body: JSON.stringify({
      outcome: "confirmed",
      reviewer: "human:reviewer",
      reason: "验收时已逐字核对后续公告中的实际指标。",
      idempotency_key: `live-forecast-actual-source-${caseId}`,
    }),
  });
  if (!review.published_source_statement?.id) {
    throw new Error(`actual-source review did not publish a SourceStatement: ${JSON.stringify(review)}`);
  }
  return review.published_source_statement.id;
}

async function prepareMarketCatalogAndHistoricalFundDisclosure({ apiBase, token, source }) {
  // Instrument and fund records enter through the public ledger command API.
  // The browser still creates every Case-specific mapping and observation.
  const company = await apiJson(apiBase, "/companies", token, {
    method: "POST",
    body: JSON.stringify({
      code: "LIVE-MARKET-001",
      name: "验收映射公司",
      type: "listed",
    }),
  });
  const stock = await apiJson(apiBase, `/companies/${company.id}/stocks`, token, {
    method: "POST",
    body: JSON.stringify({
      code: "LIVE001.SZ",
      name: "验收映射股票",
      market: "SZSE",
    }),
  });
  const fund = await apiJson(apiBase, "/funds", token, {
    method: "POST",
    body: JSON.stringify({
      code: "LIVFUND001",
      name: "验收历史披露基金",
      fund_type: "equity",
    }),
  });
  await apiJson(apiBase, `/funds/${fund.id}/holding-disclosures`, token, {
    method: "POST",
    body: JSON.stringify({
      stock_id: stock.id,
      weight: "0.035",
      report_period: "2026-06-30",
      published_at: "2026-07-20T00:00:00Z",
      source: "licensed_provider",
      source_document_version_id: source.documentVersionId,
      source_span_id: source.sourceSpanId,
      coverage_status: "complete",
    }),
  });
  return { company, stock, fund };
}

async function main() {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "fund-engine-live-ui-"));
  const apiPort = await freePort();
  const uiPort = await freePort();
  const apiBase = `http://127.0.0.1:${apiPort}/api/v1`;
  const uiBase = `http://127.0.0.1:${uiPort}`;
  const env = {
    ...process.env,
    DATABASE_URL: `sqlite:///${path.join(temporary, "live-ui.db")}`,
    RESEARCH_TENANT_TOKENS: JSON.stringify({ [token]: tenant }),
    NO_PROXY: "127.0.0.1,localhost",
    no_proxy: "127.0.0.1,localhost",
  };
  let api;
  let vite;
  let browser;
  const apiRequests = [];
  const apiResponses = [];
  const browserFailures = [];
  try {
    execFileSync(
      python,
      ["-c", "from app.models.ledger import Base; from app.db import engine; Base.metadata.create_all(engine)"],
      { cwd: backend, env, stdio: "inherit" },
    );
    api = start(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(apiPort)], { cwd: backend, env });
    await waitFor(`${apiBase}/event-research`, {
      headers: { Authorization: `Bearer ${token}` },
      label: "live API",
    });

    vite = start(process.execPath, ["./node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", String(uiPort)], {
      cwd: frontend,
      env: {
        ...env,
        VITE_BACKEND_URL: `http://127.0.0.1:${apiPort}`,
        RESEARCH_BEARER_TOKEN: token,
        VITE_RESEARCH_CLIENT: "",
      },
    });
    await waitFor(`${uiBase}/events`, { label: "default frontend" });

    browser = await chromium.launch({ channel: process.env.PW_BROWSER_CHANNEL || undefined });
    const page = await browser.newPage();
    page.on("request", (request) => {
      const url = request.url();
      if (url.startsWith(apiBase)) apiRequests.push(`${request.method()} ${url}`);
    });
    page.on("response", (response) => {
      if (response.url().startsWith(apiBase)) apiResponses.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    });
    page.on("requestfailed", (request) => {
      if (request.url().startsWith(apiBase)) browserFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "failed"}`);
    });
    page.on("console", (message) => {
      if (message.type() === "error") browserFailures.push(`console: ${message.text()}`);
    });
    const title = "真实浏览器默认 HTTP 事件验收";
    await page.goto(`${uiBase}/events/new`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "先冻结材料，再决定它属于哪个研究" }).waitFor();
    await page.getByLabel("事件原始输入").fill(`${title}。研报预计验收公司2024年归母净利润为1亿元，等待人工核验。`);
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await page.getByLabel("研究问题").waitFor();
    await page.getByRole("button", { name: "建立 Case，进入资料核验" }).click();
    await page.waitForURL(/\/events\/[0-9a-f-]{36}$/u);
    const caseId = new URL(page.url()).pathname.split("/").at(-1);
    if (!caseId) throw new Error("created Case URL did not contain an id");

    const caseReadChecks = [
      ["", () => page.getByRole("heading", { name: title }).waitFor()],
      ["/evidence", () => page.getByRole("heading", { name: "每一条关系都保留原文、时点与审核边界" }).waitFor()],
      ["/documents", () => page.getByRole("heading", { name: "原文资料" }).waitFor()],
      ["/review", () => page.getByRole("heading", { name: /条待审核关系/u }).waitFor()],
      ["/wiki", () => page.getByRole("heading", { name: "从关系回到冻结原文与审核边界" }).waitFor()],
      ["/relations", () => page.getByRole("heading", { name: "只显示与这个 Case 直接相连的研究" }).waitFor()],
    ];
    for (const [suffix, assertVisible] of caseReadChecks) {
      await page.goto(`${uiBase}/events/${caseId}${suffix}`, { waitUntil: "networkidle" });
      await assertVisible();
      if (await page.getByRole("alert").count()) {
        throw new Error(`Case page ${suffix || "/"} rendered a live-data error`);
      }
    }
    await page.goto(`${uiBase}/events/${caseId}/documents`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "从冻结资料提取候选" }).click();
    await page.getByText(/已从此冻结版本创建 \d+ 条待人工审核的原子陈述/u).waitFor();
    await page.goto(`${uiBase}/events/${caseId}/review`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "原子陈述审核" }).waitFor();
    await page.getByRole("button", { name: "在此页核对原文" }).first().click();
    await page.getByText("在此页核对的冻结原文").waitFor();
    await page.getByLabel("原子陈述审核理由").first().fill("逐字核对了冻结原文、定位、主体与来源许可。");
    await page.getByRole("button", { name: "确认并发布" }).first().click();
    await page.getByText("已发布为正式陈述；候选、原文定位和审核记录仍可回放。").waitFor();

    await page.goto(`${uiBase}/events/${caseId}/monitor/config`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "调整后会创建新的可复现版本" }).waitFor();
    const factorChoices = page
      .locator("fieldset")
      .filter({ hasText: "已确认关键因素" })
      .locator('input[type="checkbox"]');
    const factorCount = await factorChoices.count();
    if (!factorCount) throw new Error("created Case did not offer a confirmed factor for monitor configuration");
    for (let index = 1; index < factorCount; index += 1) {
      await factorChoices.nth(index).uncheck();
    }
    await page.getByLabel("频率").selectOption("daily_20_00");
    await page.getByLabel("下一验证事件").fill("下一次公司财报披露");
    await page.getByLabel("新版本变更原因").fill("为新建事件设置受控补证范围");
    await page.getByRole("button", { name: "保存为新监控版本" }).click();
    await page.getByText("当前生效版本 v1").waitFor();

    await page.goto(`${uiBase}/events/${caseId}/monitor`, { waitUntil: "networkidle" });
    await page.getByText("研究协议尚未通过，不能启动补证。").waitFor();
    await page.getByRole("button", { name: "立即补证一次" }).isDisabled().then((disabled) => {
      if (!disabled) throw new Error("strict protocol gate unexpectedly enabled a monitor run");
    });
    const { thesisId } = await prepareReadyProtocol({ apiBase, token, caseId });
    const reviewedSource = await prepareReviewedSourceStatement({ apiBase, token, caseId });
    const actualSourceStatementId = await prepareReviewedActualSourceStatement({ apiBase, token, caseId });
    const marketCatalog = await prepareMarketCatalogAndHistoricalFundDisclosure({
      apiBase,
      token,
      source: reviewedSource,
    });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "立即补证一次" }).isEnabled().then((enabled) => {
      if (!enabled) throw new Error("ready research protocol did not enable a monitor run");
    });
    await page.getByRole("button", { name: "立即补证一次" }).click();
    await page.getByRole("heading", { name: "准备研究范围 · 排队中" }).first().waitFor();
    await page.getByText("已冻结本次运行范围", { exact: true }).first().waitFor();
    await page.getByLabel("停止原因").fill("验收继续验证单因素补证，停止此前同一范围的全 Case 运行。 ");
    await page.getByRole("button", { name: "停止本次运行" }).click();
    await page
      .getByLabel("运行详情", { exact: true })
      .getByRole("heading", { name: "运行已停止 · 已停止" })
      .waitFor();

    await page.goto(`${uiBase}/events/${caseId}/market`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "从冻结原文解析关键因素" }).click();
    await page.getByLabel("解析来源原文").selectOption(reviewedSource.sourceStatementId);
    await page.getByRole("button", { name: "生成关键因素候选" }).click();
    await page.getByText("2024 年归母净利润预测兑现").waitFor();
    await page.getByRole("button", { name: "带入人工登记" }).click();
    await page.getByLabel("主张归属").waitFor();
    if (await page.getByLabel("主张表述").inputValue() !== "预计验收公司2024年归母净利润为1亿元") {
      throw new Error("parsed key-factor candidate was not carried into the human registration form");
    }
    await page.getByLabel("冻结原文陈述").waitFor();
    await page.getByLabel("主张归属").fill("验收研究员");
    await page.getByLabel("主张审核理由").fill("主张逐句回到当前 Case 冻结原文核对。 ");
    await page.getByRole("button", { name: "登记已审核主张" }).click();
    await page.getByText("已登记已审核主张").waitFor();
    await page.getByLabel("关键因素名称").fill("验收公司归母净利润预测验证");
    await page.getByLabel("验证指标").fill("归母净利润");
    await page.getByLabel("允许来源").fill("licensed_provider");
    await page.getByLabel("验证开始日期").fill("2026-01-01");
    await page.getByLabel("验证结束日期").fill("2026-12-31");
    await page.getByLabel("关联已确认命题").selectOption(thesisId);
    await page.getByLabel("支持条件").fill("已准入资料显示订单同比增长。 ");
    await page.getByLabel("反证条件").fill("已准入资料显示订单同比下降。 ");
    await page.getByLabel("下一验证事件").fill("下一次订单披露");
    await page.getByLabel("因素审核理由").fill("指标、窗口、来源和反证条件均已人工确认。 ");
    await page.getByRole("button", { name: "登记已审核关键因素" }).click();
    await page.getByText("已登记已审核关键因素").waitFor();
    await page.getByRole("button", { name: "登记历史预测验证" }).click();
    await page.getByLabel("冻结预测值").fill("100000000");
    await page.getByLabel("实体标识").fill("LIVE001.SZ");
    await page.getByLabel("单位").fill("CNY");
    await page.getByLabel("本阶段审核理由").fill("冻结研报预测的数值、主体、单位、期间及原文定位。");
    await page.getByRole("button", { name: "冻结预测目标" }).click();
    await page.getByText("已冻结预测目标").waitFor();
    await page.getByLabel("后续实际值来源").selectOption(actualSourceStatementId);
    await page.getByRole("textbox", { name: "后续实际值" }).fill("110000000");
    await page.getByLabel("本阶段审核理由").fill("冻结公告实际值，并与预测的主体、单位、期间逐项核对。");
    await page.getByRole("button", { name: "冻结后续实际值" }).click();
    await page.getByText("已冻结后续实际值").waitFor();
    await page.getByRole("button", { name: "生成机器候选" }).click();
    await page.getByText(/机器候选：得到支持/u).waitFor();
    await page.getByLabel("人工裁决理由").fill("确认机器比较规则、冻结输入与后续公告原文一致。");
    await page.getByRole("button", { name: "发布人工裁决" }).click();
    await page.getByText("已追加人工发布裁决；历史预测验证将更新。").waitFor();
    await page.getByText("得到支持", { selector: ".ros-forecast-verdict strong" }).waitFor();
    const forecastVerdicts = await apiJson(
      apiBase,
      `/research-cases/${caseId}/forecast-verdicts?cutoff=${encodeURIComponent(new Date().toISOString())}`,
      token,
    );
    const forecastVerdict = forecastVerdicts.items?.[0];
    if (
      forecastVerdict?.outcome !== "supported"
      || forecastVerdict?.forecast_source?.source_statement_id !== reviewedSource.sourceStatementId
      || forecastVerdict?.actual_source?.source_statement_id !== actualSourceStatementId
      || forecastVerdict?.inputs?.expected_value !== "100000000"
      || forecastVerdict?.inputs?.actual_value !== "110000000"
    ) {
      throw new Error(`forecast verification was not persisted as a replayable human verdict: ${JSON.stringify(forecastVerdicts)}`);
    }
    await page.getByRole("button", { name: "关联公司与股票" }).click();
    await page.getByLabel("新增关联标的").selectOption(marketCatalog.company.id);
    await page.getByLabel("标的审核理由").fill("冻结原文已明确这家公司处于订单传导范围。 ");
    await page.getByRole("button", { name: "保存已审核标的关联" }).click();
    await page.getByText("已追加已审核标的关联").first().waitFor();
    await page.goto(`${uiBase}/events/${caseId}/stocks/${marketCatalog.stock.id}`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "验收映射股票 · 股票研究档案" }).waitFor();
    await page.goto(`${uiBase}/events/${caseId}/funds/${marketCatalog.fund.id}`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "验收历史披露基金 · 基金披露档案" }).waitFor();
    await page.goto(`${uiBase}/events/${caseId}/market`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "登记基本面传导" }).click();
    await page.getByLabel("传导标的").waitFor();
    await page.getByLabel("传导机制").fill("订单增长通过履约和确认节奏传导至收入。 ");
    await page.getByLabel("传导审核理由").fill("已核对标的关系、指标口径和原文定位。 ");
    await page.getByRole("button", { name: "保存已审核基本面传导" }).click();
    await page.getByText("已追加已审核基本面传导").first().waitFor();
    await page.getByText("验收历史披露基金").first().waitFor();
    await page.getByRole("heading", { name: "建议补充的基金披露" }).waitFor();
    await page.getByLabel("补充频率").selectOption("monthly");
    await page.getByLabel("配置调整理由").fill("按月补充当前股票相关的历史基金披露");
    await page.getByRole("button", { name: "保存基金披露配置" }).click();
    await page.getByText("已保存配置版本 1").waitFor();
    await page.getByRole("button", { name: "立即补充一次" }).click();
    await page.getByText("本次补充未完成，失败原因已写入运行记录。").waitFor();
    const fundSync = await apiJson(apiBase, `/research-cases/${caseId}/fund-disclosure-sync`, token);
    const firstFundSyncRun = fundSync.runs?.[0];
    if (
      fundSync.effective_config?.frequency !== "monthly"
      || fundSync.effective_config?.fund_codes?.join(",") !== marketCatalog.fund.code
      || firstFundSyncRun?.status !== "failed"
      || firstFundSyncRun.events?.at(-1)?.stage !== "failed"
    ) {
      throw new Error(`fund disclosure task was not transparently replayable: ${JSON.stringify(fundSync)}`);
    }
    await page.getByRole("button", { name: "登记市场观测" }).click();
    await page.getByLabel("观测标的").waitFor();
    await page.getByLabel("相对表现").fill("0.012");
    await page.getByLabel("市场观测审核理由").fill("已核对窗口、基准、可得时间与价格来源。 ");
    await page.getByRole("button", { name: "保存已审核市场观测" }).click();
    await page.getByText("已追加已审核市场观测").first().waitFor();
    await page.getByRole("button", { name: "立即补证此因素" }).click();
    await page.getByText("已创建单因素补证运行", { exact: false }).waitFor();
    await page
      .getByRole("status")
      .filter({ hasText: "已创建单因素补证运行" })
      .getByRole("link", { name: "查看运行记录" })
      .click();
    await page.waitForURL(new RegExp(`/events/${caseId}/monitor$`));
    await page.getByText("已冻结本次运行范围", { exact: true }).first().waitFor();

    await page.goto(`${uiBase}/events/${caseId}/monitor/config`, { waitUntil: "networkidle" });
    await page.getByLabel("变更原因", { exact: true }).fill("等待下一次公司披露后恢复定时核验");
    await page.getByRole("button", { name: "暂停未来定时任务" }).click();
    await page.getByText("当前生效版本 v2").waitFor();
    const pausedMonitor = await apiJson(apiBase, `/research-cases/${caseId}/monitor`, token);
    if (
      pausedMonitor.monitor?.status !== "paused"
      || pausedMonitor.monitor?.version !== 2
      || pausedMonitor.monitor?.frequency !== "daily_20_00"
      || pausedMonitor.monitor?.change_reason !== "等待下一次公司披露后恢复定时核验"
    ) {
      throw new Error(`saved monitor pause was not replayable: ${JSON.stringify(pausedMonitor.monitor)}`);
    }

    await page.goto(`${uiBase}/events`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "今天，先推进哪一个判断？" }).waitFor();
    await page.getByText(title, { exact: false }).first().waitFor();
    if (apiRequests.some((request) => request.includes("client=mock"))) {
      throw new Error(`default frontend unexpectedly used mock mode: ${apiRequests.join(" | ")}`);
    }
    const expectedRequests = [
      ["POST /event-research/extract", (request) => request.startsWith("POST ") && request.endsWith("/event-research/extract")],
      ["POST /event-research", (request) => request.startsWith("POST ") && request.endsWith("/event-research")],
      ["PUT /research-cases/:caseId/monitor", (request) => request.startsWith("PUT ") && request.endsWith(`/research-cases/${caseId}/monitor`)],
      ["POST /research-cases/:caseId/monitor/runs", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/monitor/runs`)],
      ["POST /research-cases/:caseId/report-claims", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/report-claims`)],
      ["POST /research-cases/:caseId/key-factors", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/key-factors`)],
      ["POST /research-cases/:caseId/forecast-targets", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/forecast-targets`)],
      ["POST /research-cases/:caseId/actual-metric-observations", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/actual-metric-observations`)],
      ["POST /forecast-targets/:targetId/evaluate", (request) => request.startsWith("POST ") && request.includes("/forecast-targets/") && request.endsWith("/evaluate")],
      ["POST /forecast-evaluations/:candidateId/verdicts", (request) => request.startsWith("POST ") && request.includes("/forecast-evaluations/") && request.endsWith("/verdicts")],
      ["POST /research-cases/:caseId/market-instruments", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/market-instruments`)],
      ["POST /research-cases/:caseId/key-factors/:factorId/fundamental-impacts", (request) => request.startsWith("POST ") && request.includes(`/research-cases/${caseId}/key-factors/`) && request.endsWith("/fundamental-impacts")],
      ["PUT /research-cases/:caseId/fund-disclosure-sync/config", (request) => request.startsWith("PUT ") && request.endsWith(`/research-cases/${caseId}/fund-disclosure-sync/config`)],
      ["POST /research-cases/:caseId/fund-disclosure-sync/runs", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/fund-disclosure-sync/runs`)],
      ["POST /research-cases/:caseId/key-factors/:factorId/market-observations", (request) => request.startsWith("POST ") && request.includes(`/research-cases/${caseId}/key-factors/`) && request.endsWith("/market-observations")],
      ["POST /research-cases/:caseId/monitor/factor-runs", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/monitor/factor-runs`)],
      ["POST /research-cases/:caseId/monitor/paused", (request) => request.startsWith("POST ") && request.endsWith(`/research-cases/${caseId}/monitor/paused`)],
      ["GET /event-research", (request) => request.startsWith("GET ") && request.endsWith("/event-research")],
    ];
    for (const [expected, observed] of expectedRequests) {
      if (!apiRequests.some(observed)) {
        throw new Error(`missing live frontend request ${expected}: ${apiRequests.join(" | ")}`);
      }
    }
    await browser.close();
    browser = undefined;
    console.log("PASS: default frontend created, configured, registered a market factor and reviewed company-stock-fund chain, replayed a transparent fund-disclosure failure, ran, paused its future schedule, and listed the same Case through the live API");
  } catch (error) {
    const serverOutput = [api, vite]
      .filter(Boolean)
      .map((server) => [...server.stdout, ...server.stderr].join(""))
      .filter(Boolean)
      .join("\n");
    const requestOutput = apiRequests.length || apiResponses.length || browserFailures.length
      ? `\nBrowser API trace:\n${[...apiRequests, ...apiResponses, ...browserFailures].join("\n")}`
      : "";
    throw new Error(`${error instanceof Error ? error.message : error}${requestOutput}${serverOutput ? `\nServer diagnostics:\n${serverOutput}` : ""}`);
  } finally {
    await browser?.close();
    await stop(vite);
    await stop(api);
    await rm(temporary, { recursive: true, force: true });
  }
}

await main();
