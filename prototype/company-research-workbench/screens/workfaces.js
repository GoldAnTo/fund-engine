/**
 * 工作面 (§9.1 原文阅读器 + §9.2 关键输入确认)
 * 从 §9 设计原则: 严格谱系, 不暴露内部门禁对象, 允许并列保留反证。
 */
import { readerFor, criticalInputsFor, getCurrentCase } from "../fixtures.js";

export function openEvidenceReader() {
  close();
  const caseId = getCurrentCase();
  const r = readerFor(caseId);
  const html = `
    <div class="workface" role="dialog" aria-modal="true" aria-labelledby="wf-reader-title" data-workface="reader">
      <article class="workface-panel">
        <header class="wf-head">
          <div>
            <p class="eyebrow" style="margin:0">SPEC §9.1 · 精确原文阅读</p>
            <h3 id="wf-reader-title">${r.document.title} · ${r.document.locator_summary}</h3>
          </div>
          <button class="btn" onclick="closeWorkface()">关闭</button>
        </header>
        <div class="wf-body">
          ${r.lineage.permission_note ? `<p class="reader-limit-banner">${r.lineage.permission_note}</p>` : ""}
          <div class="reader-layout">
            <div>
              <div class="reader-page">
                ${r.snippet_html}
              </div>
            </div>
            <aside>
              <h4 style="margin:0 0 6px">谱系 (Source → Freeze → Locator → Atomic → 模型 → 备忘录)</h4>
              <dl class="lineage">
                <dt style="font-size:11px;color:var(--c-ink-muted)">1 · 来源登记</dt>
                <dd>
                  <div class="chain-step">
                    <h5>${r.lineage.source_register.institution}</h5>
                    <ul>
                      <li>${r.lineage.source_register.issuance}</li>
                      <li>引用: <code>${r.lineage.source_register.reference}</code></li>
                      <li>${r.lineage.source_register.license}</li>
                    </ul>
                  </div>
                </dd>
                <dt style="font-size:11px;color:var(--c-ink-muted)">2 · 冻结</dt>
                <dd>
                  <div class="chain-step">
                    <h5>原件 + 内容哈希</h5>
                    <ul>
                      <li><code>${r.lineage.frozen_artifact.hash}</code></li>
                      <li>抓取 ${r.lineage.frozen_artifact.capture_at}</li>
                      <li>解析器 ${r.lineage.frozen_artifact.parser}</li>
                    </ul>
                  </div>
                </dd>
                <dt style="font-size:11px;color:var(--c-ink-muted)">3 · 精确 locator</dt>
                <dd>
                  <div class="chain-step">
                    <h5>${r.lineage.frozen_artifact.locator}</h5>
                  </div>
                </dd>
                <dt style="font-size:11px;color:var(--c-ink-muted)">4 · 原子陈述</dt>
                <dd>
                  <div class="chain-step">
                    <h5>该材料的原子陈述 (${r.lineage.atomic_claim.length} 项)</h5>
                    <ul>
                      ${r.lineage.atomic_claim.map(c => `<li>${c.text} <small style="color:var(--c-reviewed)">· ${c.review}</small></li>`).join("")}
                    </ul>
                  </div>
                </dd>
                <dt style="font-size:11px;color:var(--c-ink-muted)">5 · 进入哪些模型输入 / 备忘录</dt>
                <dd>
                  <div class="chain-step">
                    <h5>事实 → 模型 → 备忘录</h5>
                    <ul>
                      ${r.lineage.facts_into.map(f => `<li>${f.key} <span style="color:var(--c-ink-muted)">→</span> ${f.used_in.join(", ")}</li>`).join("")}
                    </ul>
                    <p style="margin:6px 0 0;font-size:11px;color:var(--c-ink-2)">备忘录引用：</p>
                    <ul>
                      ${r.lineage.memo_use.map(s => `<li>${s}</li>`).join("")}
                    </ul>
                  </div>
                </dd>
              </dl>
            </aside>
          </div>
        </div>
        <footer class="wf-foot">
          <span class="muted" style="font-size:11px">许可范围内可展示原文; 超出部分仅保留 hash + locator 边界</span>
          <div>
            <button class="btn">复制 hash</button>
            <button class="btn btn--primary">在证据中心查看全部谱系</button>
          </div>
        </footer>
      </article>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
}

export function openCriticalInputs() {
  close();
  const caseId = getCurrentCase();
  const items = criticalInputsFor(caseId);
  const html = `
    <div class="workface" role="dialog" aria-modal="true" aria-labelledby="wf-ci-title" data-workface="inputs">
      <article class="workface-panel">
        <header class="wf-head">
          <div>
            <p class="eyebrow" style="margin:0">SPEC §9.2 · 关键输入确认（保存前阻塞队列）</p>
            <h3 id="wf-ci-title">仅列出对预测 / 价值判断有实质影响的输入 · ${items.length} 项</h3>
          </div>
          <button class="btn" onclick="closeWorkface()">关闭</button>
        </header>
        <div class="wf-body">
          <div class="banner banner--needs-input">
            <span class="b-icon">[门禁]</span>
            <div>
              <strong>关键输入</strong> 阻塞<strong>保存</strong>; 普通资料不进入此队列。
              每项需要 <code>确认</code> / <code>编辑</code> / <code>标记未知</code> / <code>接受缺口</code> / <code>补充授权资料</code>。
              所有阻塞项处理完毕才可调用发布服务冻结版本。
            </div>
          </div>

          <ul class="ci-list">
            ${items.map((ci, i) => ciRow(ci, i)).join("")}
          </ul>
        </div>
        <footer class="wf-foot">
          <span class="muted" style="font-size:11px">已决策 ${items.filter(i => i.decisions && i.decisions.length > 0).length} / ${items.length} 项 · 阻塞项 ${items.filter(i => i.blocker).length} 项</span>
          <div>
            <button class="btn" onclick="closeWorkface()">稍后处理</button>
            <button class="btn btn--primary" ${items.some(i => i.blocker && (!i.decisions || i.decisions.length === 0)) ? 'disabled title="仍存在未决策的阻塞项"' : ""}>全部处理完毕 → 提交冻结</button>
          </div>
        </footer>
      </article>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
}

function ciRow(ci, idx) {
  const isResolved = ci.decisions && ci.decisions.length > 0;
  return `
    <li class="ci-item ${ci.blocker ? (isResolved ? 'is-blocker' : 'is-needs') : ''} ${isResolved ? 'is-resolved' : ''}">
      <div>
        <div class="ci-head">
          <span class="ci-num">ci-${(idx+1).toString().padStart(2,"0")}</span>
          <strong>${ci.label}</strong>
          <span class="d-type-tag" data-type="${ci.type}">${typeLabel(ci.type)}</span>
          ${ci.blocker ? `<span class="tag tag--block" style="font-size:10px">阻塞</span>` : ""}
        </div>
        <div class="ci-impact">影响: <b>${ci.impact}</b></div>
        ${ci.decisions && ci.decisions.length > 0 ? `<div style="font-size:11px;color:var(--c-ink-muted);margin-top:6px">${ci.decisions.map(d => `· ${d.actor} <b>${d.type}</b> ${d.when}${d.note ? ' — ' + d.note : ''}`).join("<br/>")}</div>` : ""}
      </div>
      <div class="ci-now">
        <span class="label">当前值</span>
        <span class="val">${ci.now.val}</span>
        <span class="src">${ci.now.src}</span>
      </div>
      <div class="ci-actions">
        <button class="btn">确认</button>
        <button class="btn">编辑</button>
        <button class="btn">标记 Unknown</button>
        <button class="btn">接受缺口</button>
        <span class="ci-action-meta">${isResolved ? "已记录" : "未处理"}</span>
      </div>
    </li>
  `;
}

function typeLabel(t) {
  return ({ fact: "事实", assumption: "假设", derived: "推导", unknown: "未知", counter: "反证" })[t] || t;
}

function close() {
  document.querySelector(".workface")?.remove();
}

window.closeWorkface = close;

// 简单的 case 解析
function getCurrentCase2() {
  const params = new URLSearchParams(window.location.search);
  return params.get("case") === "alphabet" ? "alphabet" : "catl";
}
