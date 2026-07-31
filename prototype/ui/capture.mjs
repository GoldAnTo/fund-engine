import { createServer } from "node:http";
import { readdirSync } from "node:fs";
import { mkdtemp, readFile, realpath, stat, writeFile } from "node:fs/promises";
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

export function assertViewportFit({ scrollWidth, scrollHeight }, viewport = VIEWPORT) {
  if (scrollWidth > viewport.width) {
    throw new RangeError(`Document width ${scrollWidth} exceeds viewport width ${viewport.width}`);
  }
  if (scrollHeight > viewport.height) {
    throw new RangeError(`Document height ${scrollHeight} exceeds viewport height ${viewport.height}`);
  }
}

export function assertPngDimensions(png, viewport = VIEWPORT) {
  const signature = "89504e470d0a1a0a";
  if (png.length < 24 || png.subarray(0, 8).toString("hex") !== signature || png.subarray(12, 16).toString("ascii") !== "IHDR") {
    throw new TypeError("Capture did not produce a PNG with an IHDR header");
  }
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  if (width !== viewport.width || height !== viewport.height) {
    throw new RangeError(`PNG dimensions ${width}x${height} do not match viewport ${viewport.width}x${viewport.height}`);
  }
}

export async function captureViewportPng(page) {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
    scrollHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
  }));
  assertViewportFit(dimensions);
  const png = await page.screenshot({ type: "png", fullPage: false });
  assertPngDimensions(png);
  return png;
}

export function createTransientCaptureDirectory() {
  return mkdtemp(path.join(os.tmpdir(), "research-prototype-capture-"));
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

export function reexecWithCompatibleNode(argv = process.argv) {
  const compatibleNode = findPlaywrightNode();
  if (compatibleNode === null) return false;
  if (!compatibleNode) throw new Error("Playwright requires Node.js 20 or higher");
  const child = spawnSync(compatibleNode, argv.slice(1), { stdio: "inherit" });
  if (child.error) throw child.error;
  process.exitCode = child.status ?? 1;
  return true;
}

function isWithinRoot(candidate, rootDir) {
  return candidate === rootDir || candidate.startsWith(`${rootDir}${path.sep}`);
}

function safeFilePath(requestURL, rootDir) {
  const pathname = decodeURIComponent(new URL(requestURL, "http://localhost").pathname);
  const relative = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const candidate = path.resolve(rootDir, relative);
  return isWithinRoot(candidate, rootDir) ? candidate : null;
}

export async function startPrototypeServer({ rootDir = UI_DIR } = {}) {
  const servedRoot = await realpath(rootDir);
  const server = createServer(async (request, response) => {
    let filePath;
    try {
      filePath = safeFilePath(request.url ?? "/", servedRoot);
    } catch (error) {
      if (error instanceof URIError || error instanceof TypeError) {
        response.writeHead(400).end("Bad request");
        return;
      }
      throw error;
    }
    if (!filePath) {
      response.writeHead(403).end("Forbidden");
      return;
    }

    try {
      const resolvedFile = await realpath(filePath);
      if (!isWithinRoot(resolvedFile, servedRoot)) {
        response.writeHead(403).end("Forbidden");
        return;
      }
      const info = await stat(resolvedFile);
      if (!info.isFile()) throw new Error("Not a file");
      const body = await readFile(resolvedFile);
      response.writeHead(200, {
        "content-type": MIME_TYPES[path.extname(resolvedFile)] ?? "application/octet-stream",
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

export async function withPrototypeBrowser(callback, {
  startServer = startPrototypeServer,
  launchBrowser = launchPrototypeBrowser,
} = {}) {
  let server;
  let browser;
  let result;
  let primaryError;
  let hasPrimaryError = false;
  try {
    server = await startServer();
    browser = await launchBrowser();
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
    const page = await context.newPage();
    result = await callback({ baseURL: server.baseURL, browser, context, page });
  } catch (error) {
    primaryError = error;
    hasPrimaryError = true;
  }

  const teardownErrors = [];
  if (browser) {
    try {
      await browser.close();
    } catch (error) {
      teardownErrors.push(error);
    }
  }
  if (server) {
    try {
      await server.close();
    } catch (error) {
      teardownErrors.push(error);
    }
  }

  if (hasPrimaryError) {
    const canAttachTeardownErrors = primaryError !== null
      && (typeof primaryError === "object" || typeof primaryError === "function")
      && Object.isExtensible(primaryError);
    if (teardownErrors.length && canAttachTeardownErrors) {
      Object.defineProperty(primaryError, "teardownErrors", {
        configurable: true,
        value: teardownErrors,
      });
    }
    throw primaryError;
  }
  if (teardownErrors.length) {
    throw new AggregateError(teardownErrors, "Prototype browser teardown failed");
  }
  return result;
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
  const outputDir = selection.mode === "capture" ? await createTransientCaptureDirectory() : null;
  await withPrototypeBrowser(async ({ baseURL, page }) => {
    for (const screen of selection.screens) {
      await page.goto(`${baseURL}/?screen=${screen}`, { waitUntil: "networkidle" });
      await page.locator(`[data-screen="${screen}"]`).waitFor({ state: "visible" });
      if (selection.mode === "capture") {
        const output = path.join(outputDir, `${screen}.png`);
        const png = await captureViewportPng(page);
        await writeFile(output, png);
        console.log(`${screen} -> ${output}`);
      }
    }
  });
  if (selection.mode === "verify") console.log("shell verified at 1600x1000, DPR 1; no PNG generated");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (!reexecWithCompatibleNode()) {
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
