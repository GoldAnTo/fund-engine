/**
 * Page 4: 预测与情景 (SPEC §9)
 * 内容: 历史基线、驱动树、财务桥接、Base/Bull/Bear 假设、
 *       数值变化的影响、待确认输入。
 * 关键跳转: 从每个数值打开父输入；修改假设后触发可审计重算。
 */
import {
  catlCompany, catlResearch, catlDrivers, catlBridge, catlScenarios,
  alphaCompany, alphaResearch, alphaDrivers, alphaBridge,
} from "../fixtures.js";

export function renderForecast(state) {
  return state.caseId === "alphabet"
    ? renderAlphaForecast()
    : renderCatlForecast();
}

function renderCatlForecast() {
  return `
    <div class="workbench">
      <header class="wb-context">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${catlCompany.canonical_name} <small>${catlCompany.ticker}</small></span>
        </div>
        <div><span class="label">研究问题</span><span class="q-text">${catlResearch.question}</span></div>
        <div class="state-block">
          <span class="stage-tag"><span class="dot"></span> 预测建模</span>
          <span class="meta">FY24 → FY28 (5 年窗口)</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">04 / 07 · 预测与情景</p>
            <h1>从经营驱动到三情景预测</h1>
            <p class="muted">驱动树交代事实 / 假设 / 计算 / 未知; 财务桥接保留每个数字的父输入; 三情景必须机制不同。</p>
          </div>
          <div class="page-actions">
            <a class="btn" href="#/business?case=catl" data-nav="business">← 商业模式</a>
            <a class="btn" href="#/valuation?case=catl" data-nav="valuation">→ 价值判断</a>
          </div>
        </header>
      </section>

      <section>
        <header class="section-head">
          <h2>驱动树 · 每个数字可点击追溯</h2>
          <span class="sub">fact = 既有事实 · assumption = AI / 用户假设 · derived = 模型计算 · unknown = 缺权威</span>
        </header>
        <div class="drivers-tree">
          ${catlDrivers.map(d => driverRow(d)).join("")}
        </div>
      </section>

      <section>
        <header class="section-head">
          <h2>财务桥接 · FY24 → FY28</h2>
          <span class="sub">5 年窗口 · 单位 CNY 亿元 · derived 列由模型计算, fact 链接到证据</span>
        </header>
        <table class="bridge-table">
          <thead>
            <tr>
              <th></th>
              <th>FY24</th>
              <th>FY25</th>
              <th>FY26</th>
              <th>FY27</th>
              <th>FY28</th>
            </tr>
          </thead>
          <tbody>
            ${catlBridge.map(row => `
              <tr class="${row.type === "derived" ? "is-derive" : ""}">
                <td>
                  <div class="row-label">
                    ${rowLabel(row.fy, row.type)}
                  </div>
                </td>
                <td class="num">${row.rev.toFixed(1)}</td>
                <td class="num">${row.ebit.toFixed(1)}</td>
                <td class="num">${row.tax.toFixed(3)}</td>
                <td class="num">${row.dep.toFixed(1)}</td>
                <td class="num">${row.capex.toFixed(1)}</td>
                <td class="num">${row.d_wc.toFixed(1)}</td>
                <td class="num"><strong>${row.fcff.toFixed(1)}</strong></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
        <p class="muted" style="font-size:11px;margin-top:6px">FY26-28 是 derived · 模型版本 v2025.4-r3 · FCFF = EBIT·(1-tax) + 折旧 - capex - ΔWC</p>
      </section>

      <section>
        <header class="section-head">
          <h2>三情景 · 机制不同 · 不只是同组数字三个标签</h2>
          <span class="sub">每个情景对应一个机制, 证伪条件同时展示</span>
        </header>
        <div class="scenario-strip">
          ${Object.values(catlScenarios).map(s => scenarioBlock(s)).join("")}
        </div>
      </section>

      <section>
        <header class="section-head">
          <h2>待确认输入 · 影响最终数字</h2>
          <span class="sub">普通资料不进入阻塞; 列出的每一项都至少影响一个情景的成立条件</span>
        </header>
        <table class="bridge-table">
          <thead>
            <tr>
              <th style="width:34%">输入</th>
              <th style="width:14%">类型</th>
              <th style="width:14%">当前值</th>
              <th style="width:14%">影响</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            ${catlPendingInputs().map(ci => `
              <tr>
                <td>${ci.label}</td>
                <td><span class="d-type-tag" data-type="${ci.type}">${ci.type}</span></td>
                <td>${ci.now.val}</td>
                <td>${ci.impact}</td>
                <td>${ci.decisions && ci.decisions.length > 0
                    ? `<a href="?workface=inputs&case=catl" data-workface="inputs">已决策 ${ci.decisions.length} 项</a>`
                    : `<a href="?workface=inputs&case=catl" data-workface="inputs">去处理 →</a>`}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </section>
    </div>
  `;
}

function renderAlphaForecast() {
  return `
    <div class="workbench is-unanswerable">
      <header class="wb-context">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${alphaCompany.canonical_name} <small>${alphaCompany.ticker}</small></span>
        </div>
        <div><span class="label">研究问题</span><span class="q-text">${alphaResearch.question}</span></div>
        <div class="state-block">
          <span class="stage-tag" style="color:var(--c-failed);border-color:var(--c-failed)"><span class="dot" style="background:var(--c-failed)"></span> 财务桥接阻塞</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">04 / 07 · 预测与情景（不导出）</p>
            <h1>当前不可以导出预测</h1>
            <p class="muted">按照 SPEC §11, 当关键经营基线 (AI 推理单位毛利) 缺失时不导出财务桥接。</p>
          </div>
        </header>
      </section>

      <div class="banner banner--blocked">
        <span class="b-icon">[阻塞]</span>
        <div>
          按 SPEC §8 价值判断边界与 §11 失败处理, 我们停止于:
          <ul style="margin:8px 0 0 16px;list-style:disc">
            <li>不生成 FY26-28 财务桥接 (5 年模型要求 baseline 存在)</li>
            <li>不导出三情景数字 (Base/Bull/Bear 必须机制不同才能并列)</li>
            <li>不展示 FCFF / DCF / 企业价值数字</li>
          </ul>
          我们仍然保留已确认的部分事实 (Q1 2026 营收、Cloud 增速的口径冲突), 作为后续研究债务管理的事实层。
        </div>
      </div>

      <section>
        <header class="section-head"><h2>部分可用驱动（受披露与口径限制）</h2></header>
        <div class="drivers-tree">
          ${alphaDrivers.map(d => driverRow(d)).join("")}
        </div>
      </section>

      <section>
        <header class="section-head"><h2>为什么没有财务桥接</h2></header>
        <p class="muted">关键基线 (AI 推理单位毛利) 缺失时, 模型输出"虚拟数字"违 SPEC §8。
          这里我们只列出 <b>已确认能填入桥接的事实行</b>, 不导出影响未确认行的算式。</p>
        <table class="bridge-table" style="margin-top:6px">
          <thead>
            <tr>
              <th></th>
              <th>FY24</th><th>FY25</th><th>FY26E</th><th>FY27E</th><th>FY28E</th>
            </tr>
          </thead>
          <tbody>
            <tr><td>营收 (10-K)</td><td>$307.0bn</td><td>$350.0bn</td><td>—</td><td>—</td><td>—</td></tr>
            <tr><td colspan="6" style="color:var(--c-failed);font-size:12px;text-align:center;padding:14px">停于此处 · AI 推理单位毛利未知 · 桥接 5 年模型不适用</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  `;
}

function driverRow(d) {
  return `
    <div class="driver-row">
      <div>
        <div class="d-name">${d.label} <span class="d-type-tag" data-type="${d.type}">${d.type}</span></div>
        <small>${d.key}</small>
      </div>
      <div>
        <div class="d-type-tag" data-type="${d.type}" style="margin-bottom:6px">${typeLabel(d.type)}</div>
        <small class="muted">${typeMeta(d.type)}</small>
      </div>
      <div class="d-values">
        ${d.values.map((v, i) => `
          <div class="d-v">
            <span class="fy">${labelFY(i)}</span>
            <span class="val" data-state="${typeof v === 'number' ? 'fact' : (v === '—' ? 'gap' : 'assumption')}">${v == null ? "—" : v}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function scenarioBlock(s) {
  const eeShort = s.id === "bear" ? "贬" : s.id === "bull" ? "升" : "中";
  const eeColor = s.id === "bear" ? "var(--c-contradicts)" : s.id === "bull" ? "var(--c-reviewed)" : "var(--c-ink)";
  return `
    <article class="scenario" data-id="${s.id}">
      <header>
        <h3>${s.title} <small style="color:${eeColor};font-weight:600">EV ${eeShort}</small></h3>
        <p class="s-anchor">${s.anchor}</p>
      </header>
      <div class="s-assumptions">
        ${s.assumptions.map(a => `
          <li>
            <span class="muted">${a.k}</span>
            <span class="v">${a.v} <small class="muted">${a.note}</small></span>
          </li>
        `).join("")}
      </div>
      <div class="s-lineage">
        <strong style="font-size:11.5px">机制 ID</strong>: <code>${s.id}.${s.id === "base" ? "recovery" : s.id === "bull" ? "expansion" : "compression"}.v3</code> · 
        所有数字由 FY24→FY28 桥接在 ${s.id} 情景驱动下得出, 模型版本 v2025.4-r3
      </div>
      <div class="totals" style="display:flex;gap:8px;justify-content:space-between;border-top:1px solid var(--c-border);padding-top:6px">
        <span class="muted" style="font-size:11px">企业价值 <strong style="font-size:14px;color:var(--c-ink)">¥${s.enterprise_value.min}-${s.enterprise_value.max} ${s.enterprise_value.unit}</strong></span>
        <span class="muted" style="font-size:11px">每股 <strong style="font-size:14px;color:var(--c-ink)">¥${s.dd_cny_per_share.min}-${s.dd_cny_per_share.max}</strong></span>
      </div>
    </article>
  `;
}

function catlPendingInputs() {
  return [
    { label: "FY27 ASP 修复路径", type: "assumption", now: { val: "0.61 元/Wh" }, impact: "± ¥38/股 (Base ↔ Bear 切换)" },
    { label: "FY27 海外销售占比", type: "assumption", now: { val: "45%" }, impact: "± ¥27/股 (Bull ↔ Base)" },
    { label: "FY27 资本开支",  type: "assumption", now: { val: "¥420 亿" }, impact: "± ¥18/股 (FCFF)" },
    { label: "FY27 户用储能出货", type: "unknown", now: { val: "Unknown" }, impact: "Bear 情景置信度" },
    { label: "波兰工厂产能利用率", type: "counter", now: { val: "反证: 64%" }, impact: "海外毛利 -9%" },
  ];
}

function rowLabel(fy, type) {
  const tag = type === "derived" ? "derived" : "fact";
  return `<span class="d-type-tag" data-type="${tag}">${type}</span> &nbsp; ${fy} · ${type === "derived" ? "模型推算" : "已记录事实"}`;
}

function typeLabel(type) {
  return ({
    fact: "事实 fact",
    assumption: "假设 assumption",
    derived: "推导 derived",
    unknown: "未知 unknown",
  })[type] || type;
}
function typeMeta(type) {
  return ({
    fact: "已核对 · 来源 10-K / 季报",
    assumption: "待确认 · 来源 AI 假设 v3.2",
    derived: "模型计算 · 用 fact 或 assumption 推算",
    unknown: "缺权威公开口径",
  })[type] || "";
}
function labelFY(i) {
  return ["FY24", "FY25", "FY26E", "FY27E", "FY28E"][i] || `FY${24 + i}`;
}
