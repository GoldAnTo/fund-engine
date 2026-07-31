import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { readdirSync } from "node:fs";
import { readFile, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";

const UI_DIR = path.dirname(fileURLToPath(import.meta.url));
const VIEWPORT = { width: 1600, height: 1000 };
const FINAL_CAPTURE_TARGETS = Object.freeze({
  overview: path.resolve(UI_DIR, "../设计原型1.png"),
  "new-research": path.resolve(UI_DIR, "../设计原型3-新建研究.png"),
});
const CAPTURE_READY_SCREENS = Object.freeze(Object.keys(FINAL_CAPTURE_TARGETS));
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

function pngCrc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function assertPngDimensions(png, viewport = VIEWPORT) {
  const signature = "89504e470d0a1a0a";
  if (!Buffer.isBuffer(png) || png.length < 8 || png.subarray(0, 8).toString("hex") !== signature) {
    throw new TypeError("Capture did not produce a PNG signature");
  }

  let offset = 8;
  let header;
  let sawIend = false;
  let idatEnded = false;
  const idatParts = [];
  let chunkIndex = 0;
  while (offset < png.length) {
    if (png.length - offset < 12) throw new TypeError("PNG chunk is truncated");
    const length = png.readUInt32BE(offset);
    const dataStart = offset + 8;
    const dataEnd = dataStart + length;
    const chunkEnd = dataEnd + 4;
    if (dataEnd < dataStart || chunkEnd > png.length) throw new TypeError("PNG chunk length exceeds buffer bounds");

    const typeBuffer = png.subarray(offset + 4, offset + 8);
    const type = typeBuffer.toString("ascii");
    if (!/^[A-Za-z]{4}$/u.test(type)) throw new TypeError("PNG chunk type is invalid");
    const data = png.subarray(dataStart, dataEnd);
    const expectedCrc = png.readUInt32BE(dataEnd);
    const actualCrc = pngCrc32(Buffer.concat([typeBuffer, data]));
    if (actualCrc !== expectedCrc) throw new TypeError(`PNG ${type} chunk CRC mismatch`);

    if (chunkIndex === 0 && type !== "IHDR") throw new TypeError("PNG IHDR must be the first chunk");
    if (type === "IHDR") {
      if (header || chunkIndex !== 0 || length !== 13) throw new TypeError("PNG must contain exactly one 13-byte IHDR first");
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        compression: data[10],
        filter: data[11],
        interlace: data[12],
      };
      if (!header.width || !header.height || header.compression !== 0 || header.filter !== 0 || ![0, 1].includes(header.interlace)) {
        throw new TypeError("PNG IHDR fields are invalid");
      }
      if (header.bitDepth !== 8 || header.colorType !== 2 || header.interlace !== 0) {
        throw new TypeError("PNG must use the Playwright RGB8 non-interlaced profile");
      }
    } else if (type === "IDAT") {
      if (!header || sawIend || idatEnded) throw new TypeError("PNG IDAT chunks must be contiguous after IHDR");
      if (length > 0) idatParts.push(data);
    } else {
      if (idatParts.length > 0 && type !== "IEND") idatEnded = true;
      if (type === "IEND") {
        if (length !== 0) throw new TypeError("PNG IEND must have zero length");
        if (chunkEnd !== png.length) throw new TypeError("PNG IEND must be the final chunk at exact EOF");
        sawIend = true;
      }
    }

    offset = chunkEnd;
    chunkIndex += 1;
    if (sawIend) break;
  }

  if (!header) throw new TypeError("PNG is missing IHDR");
  if (idatParts.length === 0) throw new TypeError("PNG must contain at least one non-empty IDAT");
  if (!sawIend || offset !== png.length) throw new TypeError("PNG is missing a final IEND chunk");

  const { width, height } = header;
  if (width !== viewport.width || height !== viewport.height) {
    throw new RangeError(`PNG dimensions ${width}x${height} do not match viewport ${viewport.width}x${viewport.height}`);
  }

  const expectedLength = height * (1 + (width * 3));
  let decoded;
  try {
    decoded = inflateSync(Buffer.concat(idatParts), { maxOutputLength: expectedLength + 1 });
  } catch (error) {
    const wrapped = new TypeError("PNG IDAT zlib stream is invalid");
    wrapped.cause = error;
    throw wrapped;
  }
  if (decoded.length !== expectedLength) {
    throw new TypeError(`PNG decoded scanline length ${decoded.length} does not match expected ${expectedLength}`);
  }
  const rowLength = 1 + (width * 3);
  for (let row = 0; row < height; row += 1) {
    if (decoded[row * rowLength] > 4) throw new TypeError(`PNG scanline ${row} has an invalid filter byte`);
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

export function captureTargetForScreen(screen) {
  const target = FINAL_CAPTURE_TARGETS[screen];
  if (!target) throw new Error(`Capture renderer not implemented: ${screen}`);
  return target;
}

export async function writeFinalCaptureAtomically(target, png, {
  writeTemporary = writeFile,
  renameFile = rename,
  removeTemporary = rm,
  temporaryName = () => `.${path.basename(target)}.${process.pid}.${randomUUID()}.tmp`,
} = {}) {
  assertPngDimensions(png);
  const temporaryPath = path.join(path.dirname(target), temporaryName());
  try {
    await writeTemporary(temporaryPath, png, { flag: "wx" });
    await renameFile(temporaryPath, target);
  } finally {
    await removeTemporary(temporaryPath, { force: true });
  }
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
  await withPrototypeBrowser(async ({ baseURL, page }) => {
    for (const screen of selection.screens) {
      await page.goto(`${baseURL}/?screen=${screen}`, { waitUntil: "networkidle" });
      await page.locator(`[data-screen="${screen}"]`).waitFor({ state: "visible" });
      if (selection.mode === "capture") {
        const output = captureTargetForScreen(screen);
        const png = await captureViewportPng(page);
        await writeFinalCaptureAtomically(output, png);
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
