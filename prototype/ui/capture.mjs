import { createServer } from "node:http";
import { readdirSync } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UI_DIR = path.dirname(fileURLToPath(import.meta.url));
const VIEWPORT = { width: 1600, height: 1000 };
const CAPTURE_READY_SCREENS = ["overview"];
const MIME_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};

export function captureRemediation(kind) {
  if (kind === "dependency") return "cd frontend && npm ci";
  if (kind === "browser") {
    return "Browser runtime unavailable. Run: cd frontend && npx playwright install chromium; alternatively install system Google Chrome.";
  }
  throw new TypeError(`Unknown capture failure kind: ${kind}`);
}

function findPlaywrightNode() {
  if (Number(process.versions.node.split(".")[0]) >= 20) return null;
  const versionsDir = path.join(process.env.NVM_DIR ?? path.join(os.homedir(), ".nvm"), "versions", "node");
  let versions = [];
  try {
    versions = readdirSync(versionsDir).sort((left, right) => right.localeCompare(left, undefined, { numeric: true }));
  } catch {
    return undefined;
  }
  return versions
    .filter((version) => Number(version.replace(/^v/u, "").split(".")[0]) >= 20)
    .map((version) => path.join(versionsDir, version, "bin", "node"))
    .find(Boolean);
}

function reexecCLIForPlaywright() {
  const compatibleNode = findPlaywrightNode();
  if (compatibleNode === null) return false;
  if (!compatibleNode) throw new Error("Playwright requires Node.js 20 or higher");
  const child = spawnSync(compatibleNode, process.argv.slice(1), { stdio: "inherit" });
  if (child.error) throw child.error;
  process.exitCode = child.status ?? 1;
  return true;
}

function safeFilePath(requestURL) {
  const pathname = decodeURIComponent(new URL(requestURL, "http://localhost").pathname);
  const relative = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const candidate = path.resolve(UI_DIR, relative);
  return candidate === UI_DIR || candidate.startsWith(`${UI_DIR}${path.sep}`) ? candidate : null;
}

export async function startPrototypeServer() {
  const server = createServer(async (request, response) => {
    const filePath = safeFilePath(request.url ?? "/");
    if (!filePath) {
      response.writeHead(403).end("Forbidden");
      return;
    }

    try {
      const info = await stat(filePath);
      if (!info.isFile()) throw new Error("Not a file");
      const body = await readFile(filePath);
      response.writeHead(200, {
        "content-type": MIME_TYPES[path.extname(filePath)] ?? "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch {
      response.writeHead(404).end("Not found");
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  return {
    baseURL: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

export async function launchPrototypeBrowser() {
  let chromium;
  try {
    const playwrightURL = new URL("../../frontend/node_modules/playwright/index.mjs", import.meta.url);
    ({ chromium } = await import(playwrightURL.href));
  } catch (error) {
    const wrapped = new Error(captureRemediation("dependency"));
    wrapped.cause = error;
    throw wrapped;
  }

  try {
    return await chromium.launch({ headless: true });
  } catch (bundledError) {
    try {
      return await chromium.launch({ channel: "chrome", headless: true });
    } catch (channelError) {
      const wrapped = new Error(captureRemediation("browser"));
      wrapped.cause = new AggregateError([bundledError, channelError], "No Chromium runtime available");
      throw wrapped;
    }
  }
}

export async function withPrototypeBrowser(callback) {
  const server = await startPrototypeServer();
  let browser;
  try {
    browser = await launchPrototypeBrowser();
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
    const page = await context.newPage();
    return await callback({ baseURL: server.baseURL, browser, context, page });
  } finally {
    if (browser) await browser.close();
    await server.close();
  }
}

function parseScreens(argv) {
  const inline = argv.find((value) => value.startsWith("--screens="));
  const index = argv.indexOf("--screens");
  const value = inline?.split("=", 2)[1] ?? (index >= 0 ? argv[index + 1] : "shell");
  if (value === "shell") return { mode: "verify", screens: ["overview"] };
  if (value === "all") return { mode: "capture", screens: CAPTURE_READY_SCREENS };
  const screens = value.split(",").filter(Boolean);
  const unavailable = screens.filter((screen) => !CAPTURE_READY_SCREENS.includes(screen));
  if (unavailable.length) throw new Error(`Capture renderer not implemented: ${unavailable.join(", ")}`);
  return { mode: "capture", screens };
}

async function runCLI() {
  const selection = parseScreens(process.argv.slice(2));
  await withPrototypeBrowser(async ({ baseURL, page }) => {
    for (const screen of selection.screens) {
      await page.goto(`${baseURL}/?screen=${screen}`, { waitUntil: "networkidle" });
      await page.locator(`[data-screen="${screen}"]`).waitFor({ state: "visible" });
      if (selection.mode === "capture") {
        const output = path.join(UI_DIR, "generated", `${screen}.png`);
        await mkdir(path.dirname(output), { recursive: true });
        await page.screenshot({ path: output, fullPage: true });
        console.log(`${screen} -> ${path.relative(process.cwd(), output)}`);
      }
    }
  });
  if (selection.mode === "verify") console.log("shell verified at 1600x1000, DPR 1; no PNG generated");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (!reexecCLIForPlaywright()) {
      runCLI().catch((error) => {
        console.error(error.message);
        process.exitCode = 1;
      });
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
