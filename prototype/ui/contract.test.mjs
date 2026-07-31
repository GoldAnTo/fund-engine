import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const UI_DIR = path.dirname(fileURLToPath(import.meta.url));
const REQUIRED_SCREENS = [
  "overview",
  "new-research",
  "plan",
  "case",
  "graph",
  "review",
  "library",
  "data",
  "versions",
];
const FORBIDDEN_ASSESSMENT_PATTERNS = [
  /置信度/u,
  /成熟度/u,
  /ready_for_review/iu,
  /\b\d+(?:\.\d+)?\s*%/u,
];

function playwrightNode() {
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

function reexecForPlaywright() {
  const compatibleNode = playwrightNode();
  if (compatibleNode === null) return false;
  if (!compatibleNode) throw new Error("Playwright requires Node.js 20 or higher");
  const child = spawnSync(compatibleNode, process.argv.slice(1), { stdio: "inherit" });
  if (child.error) throw child.error;
  process.exitCode = child.status ?? 1;
  return true;
}

function selectedRoutes(argv) {
  const option = argv.find((value) => value.startsWith("--screens="));
  const positionalIndex = argv.indexOf("--screens");
  const value = option?.split("=", 2)[1]
    ?? (positionalIndex >= 0 ? argv[positionalIndex + 1] : "all");

  if (!value || value === "all") return REQUIRED_SCREENS;
  if (value === "shell") return ["overview"];

  const requested = value.split(",").filter(Boolean);
  for (const screen of requested) {
    assert.ok(REQUIRED_SCREENS.includes(screen), `Unknown screen requested: ${screen}`);
  }
  return requested;
}

async function assertSourceContract() {
  const requiredFiles = ["index.html", "styles.css", "data.js", "app.js", "capture.mjs"];
  for (const filename of requiredFiles) {
    await assert.doesNotReject(
      access(path.join(UI_DIR, filename)),
      `Missing prototype harness file: ${filename}`,
    );
  }

  const [html, app] = await Promise.all([
    readFile(path.join(UI_DIR, "index.html"), "utf8"),
    readFile(path.join(UI_DIR, "app.js"), "utf8"),
  ]);

  assert.match(html, /<main\s+id=["']app["']/u, "index.html must expose <main id=\"app\">");
  assert.match(html, /<link[^>]+href=["']\.\/styles\.css["']/u, "index.html must load ./styles.css");
  assert.match(html, /<script[^>]+src=["']\.\/data\.js["'][^>]*><\/script>/u, "index.html must load classic ./data.js");
  assert.match(html, /<script[^>]+src=["']\.\/app\.js["'][^>]*><\/script>/u, "index.html must load classic ./app.js");

  for (const screen of REQUIRED_SCREENS) {
    assert.match(app, new RegExp(`["']${screen}["']\\s*:`, "u"), `SCREEN_RENDERERS must expose ${screen}`);
  }
}

async function assertBrowserContract(routes) {
  const { withPrototypeBrowser } = await import("./capture.mjs");

  await withPrototypeBrowser(async ({ baseURL, page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 });

    for (const screen of routes) {
      await page.goto(`${baseURL}/?screen=${screen}`, { waitUntil: "networkidle" });
      const marker = page.locator(`[data-screen="${screen}"]`);
      await marker.waitFor({ state: "visible" });
      assert.equal(await marker.count(), 1, `${screen} must render exactly one [data-screen] marker`);

      const overflow = await page.evaluate(() => ({
        body: document.body.scrollWidth - document.body.clientWidth,
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }));
      assert.ok(overflow.body <= 0 && overflow.document <= 0, `${screen} has horizontal overflow: ${JSON.stringify(overflow)}`);

      const assessments = await page.locator("[data-evidence-assessment]").allTextContents();
      for (const assessment of assessments) {
        for (const forbidden of FORBIDDEN_ASSESSMENT_PATTERNS) {
          assert.doesNotMatch(assessment, forbidden, `${screen} evidence assessment contains forbidden scoring: ${forbidden}`);
        }
      }
    }
  });
}

async function main() {
  if (reexecForPlaywright()) return;
  const routes = selectedRoutes(process.argv.slice(2));
  await assertSourceContract();
  await assertBrowserContract(routes);
  console.log(`PASS prototype contract: ${routes.join(", ")}`);
}

main().catch((error) => {
  console.error(`FAIL prototype contract: ${error.message}`);
  process.exitCode = 1;
});
