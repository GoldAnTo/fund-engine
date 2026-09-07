// 截图验证:遍历 7 页 + 双案例 + 两个工作面
import { chromium } from "/Users/xiongjiali/code/fund-engine/frontend/node_modules/playwright/index.mjs";
import { mkdirSync } from "node:fs";

const OUT = "/Users/xiongjiali/code/fund-engine/prototype/company-research-workbench/screenshots";
mkdirSync(OUT, { recursive: true });

const pages = [
  { name: "01-library",   url: "http://localhost:8020/?#/library" },
  { name: "02-overview",  url: "http://localhost:8020/?#/overview" },
  { name: "03-business",  url: "http://localhost:8020/?#/business" },
  { name: "04-forecast",  url: "http://localhost:8020/?#/forecast" },
  { name: "05-valuation", url: "http://localhost:8020/?#/valuation" },
  { name: "06-evidence",  url: "http://localhost:8020/?#/evidence" },
  { name: "07-versions",  url: "http://localhost:8020/?#/versions" },
];

const alphaPages = [
  { name: "A-overview",   url: "http://localhost:8020/?case=alphabet#/overview" },
  { name: "A-business",   url: "http://localhost:8020/?case=alphabet#/business" },
  { name: "A-forecast",   url: "http://localhost:8020/?case=alphabet#/forecast" },
  { name: "A-valuation",  url: "http://localhost:8020/?case=alphabet#/valuation" },
];

const workfaces = [
  { name: "W-reader-catl",  url: "http://localhost:8020/?workface=reader&case=catl#/library" },
  { name: "W-inputs-catl",  url: "http://localhost:8020/?workface=inputs&case=catl#/library" },
  { name: "W-reader-alpha", url: "http://localhost:8020/?workface=reader&case=alphabet#/library" },
  { name: "W-inputs-alpha", url: "http://localhost:8020/?workface=inputs&case=alphabet#/library" },
];

async function shoot(browser, name, url) {
  const page = await browser.newPage({ viewport: { width: 1480, height: 900 } });
  const errors = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const file = `${OUT}/${name}.png`;
  await page.screenshot({ path: file, fullPage: true });
  const dims = await page.evaluate(() => ({
    docHeight: document.documentElement.scrollHeight,
    hasWorkbench: !!document.querySelector(".workbench"),
    h1: document.querySelector("h1")?.textContent?.trim().slice(0, 100),
    navItems: [...document.querySelectorAll(".nav-link")].map((a) => a.textContent.trim()).slice(0, 7),
    hasWorkface: !!document.querySelector(".workface"),
    isUnanswerable: !!document.querySelector(".is-unanswerable"),
  }));
  await page.close();
  return { name, url, file, errors, ...dims };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  for (const it of pages) results.push(await shoot(browser, it.name, it.url));
  for (const it of alphaPages) results.push(await shoot(browser, it.name, it.url));
  for (const it of workfaces) results.push(await shoot(browser, it.name, it.url));
  await browser.close();

  console.log("\n========== SUMMARY ==========");
  for (const r of results) {
    console.log(`\n[${r.name}] ${r.url}`);
    console.log(`  → ${r.file}`);
    console.log(`  h1: ${r.h1}`);
    console.log(`  docHeight: ${r.docHeight}px  hasWorkbench: ${r.hasWorkbench}  hasWorkface: ${r.hasWorkface}  isUnanswerable: ${r.isUnanswerable}`);
    if (r.navItems && r.navItems.length > 0) console.log(`  nav: ${r.navItems.join(" / ")}`);
    if (r.errors.length > 0) {
      console.log(`  ERRORS:`);
      r.errors.forEach((e) => console.log(`    - ${e}`));
    } else {
      console.log(`  ok`);
    }
  }
})();
