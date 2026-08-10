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

    await page.goto(`${uiBase}/events`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "今天，先推进哪一个判断？" }).waitFor();
    await page.getByText(title, { exact: false }).first().waitFor();
    if (apiRequests.some((request) => request.includes("client=mock"))) {
      throw new Error(`default frontend unexpectedly used mock mode: ${apiRequests.join(" | ")}`);
    }
    const expectedRequests = [
      ["POST /event-research/extract", (request) => request.startsWith("POST ") && request.endsWith("/event-research/extract")],
      ["POST /event-research", (request) => request.startsWith("POST ") && request.endsWith("/event-research")],
      ["GET /event-research", (request) => request.startsWith("GET ") && request.endsWith("/event-research")],
    ];
    for (const [expected, observed] of expectedRequests) {
      if (!apiRequests.some(observed)) {
        throw new Error(`missing live frontend request ${expected}: ${apiRequests.join(" | ")}`);
      }
    }
    await browser.close();
    browser = undefined;
    console.log("PASS: default frontend created and listed the same Case through the live API");
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
