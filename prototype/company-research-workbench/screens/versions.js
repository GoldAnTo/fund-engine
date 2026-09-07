/**
 * Page 7: 备忘录与版本 (SPEC §9)
 * 内容: 按"边界 → 商业 → 预测与价值 → 反证与下一步"阅读的完整备忘录、
 *       确认记录、冻结版本、差异、回放与导出。
 * 关键跳转: 从每段打开证据；比较严格祖先与后继版本。
 */
import { memoFor, versionsFor } from "../fixtures.js";

const PAGES = [
  { num: "01", label: "研究库" },
  { num: "02", label: "研究概览" },
  { num: "03", label: "商业模式" },
  { num: "04", label: "预测与情景" },
  { num: "05", label: "价值判断" },
  { num: "06", label: "证据中心" },
  { num: "07", label: "备忘录与版本" },
];

export function renderVersions(state) {
  const memo = memoFor(state.caseId);
  const versions = versionsFor(state.caseId);
  return `
    <div class="workbench">
      <header class="wb-context">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${state.caseId === 'alphabet' ? 'Alphabet Inc. GOOGL' : '宁德时代 300750.SZ'}</span>
        </div>
        <div><span class="label">备忘录</span><span class="q-text">完整阅读路径（边界 → 商业 → 预测与价值 → 反证与下一步）</span></div>
        <div class="state-block">
          <span class="stage-tag"><span class="dot"></span> 备忘录与版本</span>
          <span class="meta">回放与差异</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">07 / 07 · 备忘录与版本</p>
            <h1>完整研究备忘录</h1>
            <p class="muted">可回放版本; 反证与未知与支持证据并列保留, 不被摘要省略。</p>
          </div>
          <div class="page-actions">
            <button class="btn" id="export-markdown" data-action="export-md">导出 Markdown</button>
            <button class="btn btn--primary" id="freeze-version" data-action="freeze-v${versions.length + 1}" ${state.caseId === 'alphabet' ? 'disabled title="阻塞不可保存"' : ''}>冻结当前为 v${versions.length + 1}</button>
          </div>
        </header>
      </section>

      <div class="page-overview">
        <article class="memo" id="memo">${renderMemoBody(memo)}</article>

        <aside class="shoulder">
          <div class="shoulder-card">
            <h3>导航</h3>
            <ol class="memo-nav">
              ${memoSections(memo).map(s => `
                <li><a href="#${s.id}">${s.num} · ${s.label}</a></li>
              `).join("")}
            </ol>
          </div>
          <div class="shoulder-card">
            <h3>版本链 (祖先 ↔ 后继)</h3>
            <div class="draft-versions">
              ${versions.map(v => versionRow(v)).join("")}
            </div>
          </div>
          <div class="shoulder-card">
            <h3>回放与差异</h3>
            <p class="muted" style="font-size:12px">任意两个 frozen 版本可求差异, 包括事实/驱动/反证/研究债务四类。</p>
            <button class="btn" disabled>比较 v2 ↔ v3</button>
          </div>
        </aside>
      </div>
    </div>
  `;
}

function renderMemoBody(md) {
  // 轻量 Markdown 渲染: 段落 + 标题 + 引用
  const lines = md.split("\n");
  let html = "";
  let inList = false;
  let listType = "ul";
  let para = [];
  let sectionIdx = 0;
  const sections = [];

  function closePara() {
    if (para.length) {
      html += `<p>${inline(para.join(" "))}</p>`;
      para = [];
    }
  }
  function closeList() {
    if (inList) {
      html += `</${listType}>`;
      inList = false;
    }
  }

  for (const ln of lines) {
    const t = ln.trim();
    if (t.startsWith("# ")) {
      closePara(); closeList();
      html += `<div class="memo-head"><h1>${inline(t.slice(2))}</h1>`;
    } else if (t.startsWith("---")) {
      closePara(); closeList();
      html += `<hr/></div>`;
    } else if (t.startsWith("**") && !t.includes("**:")) {
      // meta 行
      const m = t.match(/^\*\*(.+?):\*\*\s*(.+)$/);
      if (m) {
        html += `<p class="memo-meta"><strong>${m[1]}:</strong> ${m[2]}</p>`;
      }
    } else if (t.startsWith("## §")) {
      closePara(); closeList();
      const secId = `sec-${sectionIdx}`;
      const m = t.match(/§(\d+)\s*·\s*(.+)/);
      html += `<section class="memo-section" id="${secId}"><div class="sec-num">§${m ? m[1] : sectionIdx}</div><div>`;
      html += `<h3 id="${secId}-h">${m ? m[2] : t.replace("##", "")}</h3>`;
      sections.push({ id: secId + "-h", num: "§" + (m ? m[1] : sectionIdx), label: m ? m[2] : "" });
      sectionIdx++;
    } else if (t.startsWith("## ")) {
      closePara(); closeList();
      html += `<h2>${inline(t.slice(3))}</h2>`;
    } else if (t.startsWith("### ")) {
      closePara(); closeList();
      html += `<h3>${inline(t.slice(4))}</h3>`;
    } else if (/^[-*]\s+/.test(t)) {
      closePara();
      if (!inList) { html += `<ul class="memo-list">`; inList = true; listType = "ul"; }
      html += `<li>${inline(t.replace(/^[-*]\s+/, ""))}</li>`;
    } else if (!t) {
      closePara(); closeList();
    } else {
      closeList();
      para.push(t);
    }
  }
  closePara(); closeList();
  // 关闭最后一个 section
  html += `</div></section>`;
  // 但如果上一步加多了, 修整
  html = html.replace(/<\/div><\/section>(<p|$)/, "</div></section>");
  return html;
}

function inline(text) {
  // 引用 [^xx]
  return text
    .replace(/\[\^([^\]]+)\]/g, (m, k) => {
      // 由于是文档内的脚注, 用 cite 展示
      return `<a class="cite" data-open="reader" title="查看原文">${k}</a>`;
    })
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function memoSections(md) {
  const sections = [];
  const lines = md.split("\n");
  for (const ln of lines) {
    if (ln.trim().startsWith("## §")) {
      const m = ln.trim().match(/§(\d+)\s*·\s*(.+)/);
      if (m) sections.push({ num: "§" + m[1], label: m[2] });
    }
  }
  return sections;
}

function versionRow(v) {
  // 找下一个 frozen 版本用于差异配对
  const allVersions = versionsFor("catl");
  const nextFrozen = allVersions.find(x => x.is_frozen && x.seq > v.seq);
  const diffPair = v.is_frozen && nextFrozen ? `${v.seq}-${nextFrozen.seq}` : null;
  return `
    <article class="version-row">
      <div class="v-head">
        <strong>v${v.seq}</strong>
        <span class="v-id"><code>${v.id}</code></span>
      </div>
      <p class="v-time" style="font-size:11px">${v.timestamp} · basis <code>${v.basis}</code> · cutoff ${v.cutoff}</p>
      <div class="v-summary">
        ${v.summary_tags.map(t => `<span class="tag">${t}</span>`).join(" ")}
      </div>
      <div class="v-actions">
        <span class="tag ${v.is_frozen ? 'tag--ok' : 'tag--pending'}">${v.is_frozen ? "已冻结" : "Draft"}</span>
        <button class="btn" data-action="replay-catl-${v.seq}">回放</button>
        ${diffPair ? `<button class="btn" data-action="diff-catl-${diffPair}">差异</button>` : ""}
      </div>
    </article>
  `;
}
