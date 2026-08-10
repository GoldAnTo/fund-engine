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
        VITE_RESEARCH_API_URL: apiBase,
        VITE_RESEARCH_BEARER_TOKEN: token,
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
    await page.getByLabel("事件原始输入").fill(`${title}。公司披露新的经营数据，等待人工核验。`);
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await page.getByLabel("研究问题").waitFor();
    await page.getByRole("button", { name: "建立 Case，进入资料核验" }).click();
    await page.waitForURL(/\/events\/[0-9a-f-]{36}$/u);
    const caseId = new URL(page.url()).pathname.split("/").at(-1);
    if (!caseId) throw new Error("created Case URL did not contain an id");

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
    await page.getByLabel("下一验证事件").fill("下一次公司财报披露");
    await page.getByLabel("新版本变更原因").fill("为新建事件设置受控补证范围");
    await page.getByRole("button", { name: "保存为新监控版本" }).click();
    await page.getByText("当前生效版本 v1").waitFor();

    await page.goto(`${uiBase}/events/${caseId}/monitor`, { waitUntil: "networkidle" });
    await page.getByText("研究协议尚未通过，不能启动补证。").waitFor();
    await page.getByRole("button", { name: "立即补证一次" }).isDisabled().then((disabled) => {
      if (!disabled) throw new Error("strict protocol gate unexpectedly enabled a monitor run");
    });
    await prepareReadyProtocol({ apiBase, token, caseId });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "立即补证一次" }).isEnabled().then((enabled) => {
      if (!enabled) throw new Error("ready research protocol did not enable a monitor run");
    });
    await page.getByRole("button", { name: "立即补证一次" }).click();
    await page.getByRole("heading", { name: "准备研究范围 · 排队中" }).first().waitFor();
    await page.getByText("已冻结本次运行范围", { exact: true }).first().waitFor();

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
      ["GET /event-research", (request) => request.startsWith("GET ") && request.endsWith("/event-research")],
    ];
    for (const [expected, observed] of expectedRequests) {
      if (!apiRequests.some(observed)) {
        throw new Error(`missing live frontend request ${expected}: ${apiRequests.join(" | ")}`);
      }
    }
    await browser.close();
    browser = undefined;
    console.log("PASS: default frontend created, configured, ran, and listed the same Case through the live API");
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
