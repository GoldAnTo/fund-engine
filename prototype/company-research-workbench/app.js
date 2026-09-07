/**
 * App entry. URL 状态机 + 通用外壳 + 每个页面的渲染与工作面管理。
 */
import { renderLibrary } from "./screens/library.js";
import { renderOverview } from "./screens/overview.js";
import { renderBusiness } from "./screens/business.js";
import { renderForecast } from "./screens/forecast.js";
import { renderValuation } from "./screens/valuation.js";
import { renderEvidence } from "./screens/evidence.js";
import { renderVersions } from "./screens/versions.js";
import { openCriticalInputs, openEvidenceReader } from "./screens/workfaces.js";
import { memoFor, versionsFor, criticalInputsFor, evidenceFor } from "./fixtures.js";

const ROUTES = {
  library: renderLibrary,
  overview: renderOverview,
  business: renderBusiness,
  forecast: renderForecast,
  valuation: renderValuation,
  evidence: renderEvidence,
  versions: renderVersions,
};

// 7 个固定页面对应导航项（与 SPEC §9 一致）
const NAV_ITEMS = [
  { num: "01", page: "library",   label: "研究库",     hash: "#/library" },
  { num: "02", page: "overview",  label: "研究概览",   hash: "#/overview" },
  { num: "03", page: "business",  label: "商业模式",   hash: "#/business" },
  { num: "04", page: "forecast",  label: "预测与情景", hash: "#/forecast" },
  { num: "05", page: "valuation", label: "价值判断",   hash: "#/valuation" },
  { num: "06", page: "evidence",  label: "证据中心",   hash: "#/evidence" },
  { num: "07", page: "versions",  label: "备忘录与版本", hash: "#/versions" },
];

const app = document.querySelector("#app");
let currentAbortController = null;

function getState() {
  const params = new URLSearchParams(window.location.search);
  const hash = (window.location.hash || "#/library").replace(/^#\/?/, "");
  return {
    caseId: params.get("case") === "alphabet" ? "alphabet" : "catl",
    page: hash || "library",
    workface: params.get("workface"),
  };
}

function navigate({ page, caseId, workface }) {
  const url = new URL(window.location.href);
  const params = new URLSearchParams(url.search);
  if (caseId) {
    if (caseId === "catl") params.delete("case");
    else params.set("case", caseId);
  }
  url.search = params.toString();
  url.hash = `#/${page || "library"}`;
  window.history.pushState({}, "", url);
  if (workface === "inputs") {
    openCriticalInputs();
    return;
  }
  if (workface === "reader") {
    openEvidenceReader();
    return;
  }
  closeWorkface();
  render({ focusHeading: true });
}

function navigateFromHref(page, searchParams) {
  const params = new URLSearchParams(window.location.search);
  const caseId = searchParams.get("case");
  if (caseId) params.set("case", caseId);
  const search = params.toString();
  const url = (search ? `?${search}` : "") + `#/${page}`;
  window.history.pushState({}, "", url);
  closeWorkface();
  render({ focusHeading: true });
}

window.addEventListener("popstate", () => {
  render({ focusHeading: true });
});

function closeWorkface() {
  document.querySelector(".workface")?.remove();
}

function renderTopbar(state) {
  const info = state.caseId === "catl"
    ? { name: "宁德时代 300750.SZ", cutoff: "2026-06-30 16:00 CST" }
    : { name: "Alphabet Inc. GOOGL", cutoff: "2026-06-30 16:00 ET" };
  const stageLabel = state.caseId === "catl"
    ? { label: "已完成 · 可保存", cls: "completed" }
    : { label: "阻塞 · 17 项缺口", cls: "blocked" };

  return `
    <header class="topbar" role="banner">
      <a class="brand" href="/" aria-label="公司研究工作台首页">
        <span class="brand-mark" aria-hidden="true">研</span>
        <span>公司研究工作台</span>
      </a>
      <nav class="topbar-context" aria-label="研究上下文">
        <div><strong>研究对象:</strong> ${info.name}</div>
        <div><strong>基础截止:</strong> ${info.cutoff}</div>
        <div>
          <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${state.caseId === 'catl' ? 'var(--c-reviewed)' : 'var(--c-failed)'};margin-right:6px"></span>
          <strong>状态:</strong> ${stageLabel.label}
        </div>
      </nav>
      <div class="topbar-actions">
        <a href="?case=${state.caseId === 'catl' ? 'alphabet' : 'catl'}${state.page === 'library' ? '' : `&page=${state.page}`}" class="btn btn--ghost">切到${state.caseId === 'catl' ? 'Alphabet 不可回答案例' : 'CATL 可回答案例'}</a>
        <span class="account"><span class="account-avatar" aria-hidden="true">XJ</span><span>研究员</span></span>
      </div>
    </header>
  `;
}

function renderNav(state) {
  const items = NAV_ITEMS.map((it) => {
    const active = state.page === it.page;
    const url = `${it.route}${state.caseId === 'catl' ? '' : '?case=alphabet'}`;
    return `
      <li>
        <a class="nav-link${active ? ' is-active' : ''}" href="${it.hash}" aria-current="${active ? 'page' : 'false'}">
          <span class="num">${it.num}</span>
          <span>${it.label}</span>
        </a>
      </li>
    `;
  }).join("");
  return `
    <aside class="nav" aria-label="工作台主导航">
      <p class="nav-group-label">7 个研究页面</p>
      <ul>${items}</ul>
      <div class="nav-footer">
        <p>不可变历史版本 · 每篇备忘录独立冻结</p>
        <p style="margin-top:6px"><a href="?workface=inputs&case=${state.caseId}">⚑ 关键输入确认${state.caseId === 'catl' ? '' : ''}</a></p>
      </div>
    </aside>
  `;
}

function render(ctx = {}) {
  const state = getState();
  const renderer = ROUTES[state.page] || renderLibrary;

  // 撤销上一次 bindInternal 注册的 listeners
  if (currentAbortController) currentAbortController.abort();
  currentAbortController = new AbortController();

  // 共享外壳
  app.innerHTML = `
    ${renderTopbar(state)}
    ${renderNav(state)}
    <div class="main">
      ${renderDisclosure()}
      ${renderer(state, ctx)}
    </div>
  `;

  // 工作面 (默认从 URL 读取 ?workface=)
  if (state.workface === "inputs") openCriticalInputs();
  if (state.workface === "reader") openEvidenceReader();

  // 自动滚动到顶部 + focus
  if (ctx.focusHeading) {
    const heading = app.querySelector("h1");
    heading?.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }

  // 绑定内部 jump 链接
  bindInternal(state);
}

function renderDisclosure() {
  return `
    <p class="prototype-disclosure" data-prototype-disclosure>
      <strong>交互原型</strong>
      公司、市场与估值数字均为模拟示例，不代表已核验外部披露，不构成投资建议
      <span class="divider">·</span>
      CATL / Alphabet 双案例对照
      <span class="divider">·</span>
      严格按 SPEC §9 7 页 + §9.1/§9.2 工作面组织
    </p>
  `;
}

function bindInternal(state) {
  if (!currentAbortController) return;
  const signal = currentAbortController.signal;
  app.addEventListener("click", (e) => {
    const action = e.target.closest("[data-action]");
    if (action) {
      e.preventDefault();
      e.stopPropagation();
      const which = action.dataset.action;
      if (which === "export-md") exportMemoMarkdown(state);
      else if (which.startsWith("replay-")) replayVersion(state, which.replace("replay-", ""));
      else if (which.startsWith("diff-")) diffVersions(state, which.replace("diff-", ""));
      else if (which.startsWith("freeze-")) freezeVersion(state);
      return;
    }
    const target = e.target.closest("[data-nav]");
    if (target) {
      e.preventDefault();
      e.stopPropagation();
      navigate({ page: target.dataset.nav, caseId: state.caseId });
      return;
    }
    const wf = e.target.closest("[data-workface]");
    if (wf) {
      e.preventDefault();
      e.stopPropagation();
      if (wf.dataset.workface === "inputs") openCriticalInputs();
      else if (wf.dataset.workface === "reader") openEvidenceReader();
      return;
    }
  }, { signal });
}

// 全局拦截 hash 链接以确保 SPA 行为
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[href]');
  if (!a) return;
  const hrefAttr = a.getAttribute("href");
  // hash 链接: #/path
  if (hrefAttr && hrefAttr.startsWith("#/")) {
    e.preventDefault();
    const url = new URL(a.href);
    const page = (url.hash || "#/library").replace(/^#\/?/, "");
    const params = new URLSearchParams(url.search);
    const currentParams = new URLSearchParams(window.location.search);
    if (params.get("case")) currentParams.set("case", params.get("case"));
    if (currentParams.toString()) {
      window.history.pushState({}, "", `?${currentParams.toString()}#/${page}`);
    } else {
      window.history.pushState({}, "", `#/${page}`);
    }
    closeWorkface();
    render({ focusHeading: true });
    return;
  }
  // 内部 path 链接 (常见来自 research_card / recent_row / shoulder): /path?case=...
  if (hrefAttr && /^\/[a-z]+(\?|$)/.test(hrefAttr)) {
    e.preventDefault();
    const url = new URL(a.href, window.location.origin);
    const page = url.pathname.replace(/^\//, "");
    const params = url.searchParams;
    navigateFromHref(page, params);
    return;
  }
  // 版本链操作（回放 / 差异 / 冻结）
  const action = e.target.closest("[data-action]");
  if (action) {
    e.preventDefault();
    const state = getState();
    if (action.dataset.action === "export-md") {
      exportMemoMarkdown(state);
    } else if (action.dataset.action.startsWith("replay-")) {
      const v = action.dataset.action.replace("replay-", "");
      replayVersion(state, v);
    } else if (action.dataset.action.startsWith("diff-")) {
      const pair = action.dataset.action.replace("diff-", ""); // e.g. "2-3"
      diffVersions(state, pair);
    } else if (action.dataset.action.startsWith("freeze-")) {
      freezeVersion(state);
    }
    return;
  }
  // 任何页面中出现的 "查看依据" 按钮 -> 打开阅读器
  const t = e.target.closest("[data-open='reader']");
  if (t) {
    e.preventDefault();
    openEvidenceReader();
  }
});

// ============ 导出 / 冻结 / 回放 / 差异 ============

// 真实 SHA-256 (浏览器 SubtleCrypto, 支持时使用; 否则同步实现)
async function sha256(text) {
  if (crypto?.subtle?.digest) {
    try {
      const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
      return "0x" + Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
    } catch {
      // fall through
    }
  }
  // 紧凑的 SHA-256 实现 (FIPS 180-4) — 仅原型使用
  const bytes = new TextEncoder().encode(text);
  const len = bytes.length;
  // pad
  const pad = new Uint8Array((((len + 9 + 63) >> 6) << 6));
  pad.set(bytes);
  pad[len] = 0x80;
  const bitlen = BigInt(len) * 8n;
  const dv = new DataView(pad.buffer);
  dv.setBigUint64(pad.length - 8, bitlen, false);

  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  let H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
  const rotr = (x, n) => ((x >>> n) | (x << (32 - n))) >>> 0;
  for (let off = 0; off < pad.length; off += 64) {
    const W = new Array(64);
    for (let i = 0; i < 16; i++) W[i] = dv.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(W[i-15], 7) ^ rotr(W[i-15], 18) ^ (W[i-15] >>> 3);
      const s1 = rotr(W[i-2], 17) ^ rotr(W[i-2], 19) ^ (W[i-2] >>> 10);
      W[i] = (W[i-16] + s0 + W[i-7] + s1) >>> 0;
    }
    let [a,b,c,d,e,f,g,h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ ((~e >>> 0) & g);
      const t1 = (h + S1 + ch + K[i] + W[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const mj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + mj) >>> 0;
      h = g; g = f; f = e;
      e = (d + t1) >>> 0;
      d = c; c = b; b = a;
      a = (t1 + t2) >>> 0;
    }
    H = H.map((v, i) => (v + [a,b,c,d,e,f,g,h][i]) >>> 0);
  }
  return "0x" + H.map(v => v.toString(16).padStart(8, "0")).join("");
}

function downloadBlob(filename, content, mime = "text/markdown") {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

async function exportMemoMarkdown(state) {
  const memo = memoFor(state.caseId);
  const evidence = evidenceFor(state.caseId);
  const inputs = criticalInputsFor(state.caseId);
  const company = state.caseId === "alphabet" ? "alphabet" : "catl";

  // 包裹 frontmatter + 主体 + 证据清单
  const header = [
    "---",
    `case: ${company}`,
    `exported_at: ${new Date().toISOString()}`,
    `basis: ${state.caseId === 'catl' ? 'basis-catl-2026q2' : 'basis-alph-2026q2'}`,
    `cutoff_at: ${state.caseId === 'catl' ? '2026-06-30T16:00:00+08:00' : '2026-06-30T16:00:00-04:00'}`,
    `answerability: ${state.caseId === 'catl' ? 'answerable' : 'not_answerable'}`,
    `evidence_count: ${evidence.length}`,
    `critical_input_count: ${inputs.length}`,
    "---",
    "",
  ].join("\n");

  const evidenceBlock = [
    "",
    "## 附录 · 证据清单 (来源 → locator → 内容哈希)",
    "",
    ...evidence.map(e => `- **${e.type}** — ${e.text}\n    来源: ${e.srcRole} · ${e.locator} · \`${e.hash}\``),
    "",
  ].join("\n");

  const content = header + memo + evidenceBlock;
  const hash = await sha256(content);
  const filename = `${company}-research-${hash.slice(2, 14)}-${crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)}.md`;
  // 哈希也写一份 "manifest sidecar" 进入 prototype 控制台 (横幅提示)
  showExportBanner(filename, hash, content.length);
  downloadBlob(filename, content);
  // 把这次导出的元数据放进 sessionStorage, 供 e2e 验证
  sessionStorage.setItem("wb.lastExport", JSON.stringify({ filename, hash, bytes: content.length, at: new Date().toISOString() }));
}

function showExportBanner(filename, hash, bytes) {
  let banner = document.querySelector(".export-toast");
  if (banner) banner.remove();
  banner = document.createElement("div");
  banner.className = "export-toast banner banner--info";
  banner.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:300;font-size:12px;";
  banner.innerHTML = `
    <span class="b-icon">[↧]</span>
    <div>
      <strong>导出完成:</strong> <code>${filename}</code><br/>
      内容哈希 <code>${hash}</code> · ${bytes} 字节
    </div>
  `;
  document.body.appendChild(banner);
  setTimeout(() => banner.remove(), 6000);
}

async function freezeVersion(state) {
  if (state.caseId === "alphabet") return; // 阻塞场景冻结按钮已 disabled
  const versions = versionsFor(state.caseId);
  const seq = versions.length + 1;
  const memo = memoFor(state.caseId);
  const inputs = criticalInputsFor(state.caseId);
  const evidence = evidenceFor(state.caseId);
  const manifest = {
    seq,
    basis: "basis-catl-2026q2",
    cutoff_at: "2026-06-30T16:00:00+08:00",
    strategy_version: "v2025.4",
    model_version: "v2025.4-r3",
    memo_text: memo,
    critical_inputs: inputs.map(i => ({
      key: i.key, label: i.label, type: i.type, blocker: i.blocker,
      now: i.now, impact: i.impact,
      decisions: i.decisions || [],
    })),
    evidence_facts: evidence.map(e => ({
      type: e.type, text: e.text, srcRole: e.srcRole, locator: e.locator, hash: e.hash,
    })),
    frozen_at: new Date().toISOString(),
  };
  const manifestText = JSON.stringify(manifest, null, 2);
  const hash = await sha256(manifestText);

  // 追加新版本 (内存里)
  const allFrozen = JSON.parse(sessionStorage.getItem("wb.frozen") || "[]");
  const entry = { seq, hash, basis: manifest.basis, cutoff_at: manifest.cutoff_at, frozen_at: manifest.frozen_at, byte_length: manifestText.length };
  allFrozen.push(entry);
  sessionStorage.setItem("wb.frozen", JSON.stringify(allFrozen));

  showExportBanner(`v${seq} 冻结成功 · 清单哈希 ${hash}`, hash, manifestText.length);
  // 实际环境下会调用 publication service — 这里只展示给审计
  window.__lastFrozen = { entry, manifest };
}

function replayVersion(state, versionKey) {
  // e.g. "catl-3" → 找到该版本的可读副本, 显示一个 toast
  showExportBanner(`回放 v${versionKey} · 父版本哈希已核对`, "—", 0);
}

function diffVersions(state, pair) {
  const [a, b] = pair.split("-");
  showExportBanner(`v${a} ↔ v${b} · 计算差异 (事实 / 驱动 / 反证 / 研究债务)`, "—", 0);
}

render();
