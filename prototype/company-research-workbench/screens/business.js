/**
 * Page 3: 商业模式 (SPEC §9)
 * 内容: 业务单元、产品/客户、收入/定价、成本、资本占用、竞争位置、
 *       机制、关键 KPI、反证。
 * 关键跳转: 任意模块或 KPI 打开对应证据。
 */
import { catlCompany, catlBusiness, catlResearch, alphaCompany, alphaBusiness, alphaResearch } from "../fixtures.js";

export function renderBusiness(state) {
  return state.caseId === "alphabet"
    ? renderAlphaBusiness()
    : renderCatlBusiness();
}

function renderCatlBusiness() {
  const b = catlBusiness;
  return `
    <div class="workbench">
      <header class="wb-context" aria-label="研究上下文">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${catlCompany.canonical_name} <small>${catlCompany.ticker}</small></span>
          <span class="meta">${catlCompany.industry}</span>
        </div>
        <div>
          <span class="label">研究问题</span>
          <span class="q-text">${catlResearch.question}</span>
        </div>
        <div class="state-block">
          <span class="stage-tag"><span class="dot"></span> 商业模式</span>
          <span class="meta">${catlResearch.cutoff_at}</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">03 / 07 · 商业模式</p>
            <h1>宁德时代如何赚钱, 以及什么会改变结果</h1>
            <p class="muted">从客户结构 → 单 GWh 经济 → 资本支出 → 周期, 而不是从行业叙事开始。</p>
          </div>
          <div class="page-actions">
            <a class="btn" href="#/forecast?case=catl" data-nav="forecast">→ 预测与情景</a>
            <a class="btn" href="#/evidence?case=catl" data-nav="evidence">证据中心</a>
          </div>
        </header>

        <p style="margin-top:8px;color:var(--c-ink-2);font-size:13.5px;max-width:84ch">
          CATL 的核心机制不依赖单一产品或客户, 而是"全球客户结构 + 一体化材料 + 海外建厂"锁定单 GWh 毛利,
          并通过储能 IRR 套利将毛利转化为运营现金流。ASP 持续下行已经导致单位毛利占比下降约 4.1pct,
          这是当前研究问题的最大不确定来源。
        </p>
      </section>

      ${businessModules(b)}

      <section>
        <header class="section-head"><h2>关键 KPI · 一行 · 全部可被点击追溯到证据</h2><span class="sub">点击查看依据</span></header>
        <div class="kpi-strip">
          ${kpi("全球装机份额 (Q1 2026)", "37.4%", "同比 +2.1pct", "delta", true)}
          ${kpi("单 GWh 毛利 (FY25)", "≈ 1.21 亿元", "ASP 持续下行", null, false)}
          ${kpi("海外销售占比 (TTM)", "38.7%", "剔除本土", "delta", true)}
          ${kpi("现金循环周期 (Q1 2026)", "47 天", "改善 11 天", "delta", true)}
          ${kpi("产能利用率 (FY25)", "81%", "—", null, true)}
          ${kpi("研发费率 (FY25)", "6.6%", "—", null, true)}
        </div>
      </section>

      ${mechanismsBlock(b)}

      ${counterEvidenceBlock(b)}

      <section class="page-overview">
        <div class="business-narrative">
          <section class="narrative-block">
            <h3><span class="ix">01</span> 单 GWh 单位经济</h3>
            <p>2025 单 GWh 不含税收入 ≈ 6.8 亿元, 单 GWh 毛利 ≈ 1.21 亿元。
              收入主要由销量 × 单车带电量 × ASP 推动; 客户结构目前集中在主流主机厂, 海外占比 38.7%。</p>
            <p>价格战 (2025-2026) 已经把 ASP 从 0.78 拉到 0.62 元/Wh, 海外 ASP 仍大致稳定。
              储存 IRR 套利窗口也缩窄, 国内储能现货价格已经下行至 0.78 元/Wh, 这部分不是"想象空间", 而是已可观测的现金流。</p>
            <dl class="fact-chain">
              <dt>销量驱动</dt><dd>EV 渗透率 × 单车带电量 (FY25 行业 588 GWh → FY28E 955 GWh)</dd>
              <dt>ASP 驱动</dt><dd>价格战 + 一体化材料替代 (FY27E 修复至 0.61 元/Wh)</dd>
              <dt>成本驱动</dt><dd>材料一体化 + 产能利用率 (FY25 81%)</dd>
              <dt>现金驱动</dt><dd>现金循环周期 FY25 末 47 天 (改善 11 天)</dd>
            </dl>
          </section>

          <section class="narrative-block">
            <h3><span class="ix">02</span> 资本与汇率</h3>
            <p>CATL 单一深交所证券, 单货币 CNY。资本开支主要为国内外工厂扩建 + 研发;
              FY26 管理层指引 380-440 亿元。
              汇率假设主要影响海外销售折算, FY26 模型采用 PBoC 中间价 USD/CNY = 7.18 (来源 PBoC 2026-06-30)。</p>
            <dl class="fact-chain">
              <dt>FY25 资本开支</dt><dd>¥38.9 bn (实际)</dd>
              <dt>FY26 指引</dt><dd>¥38-44 bn</dd>
              <dt>FX 假设</dt><dd>USD/CNY = 7.18 (PBoC 中间价)</dd>
            </dl>
          </section>

          <section class="narrative-block">
            <h3><span class="ix">03</span> 竞争位置与边界条件</h3>
            <p>CATL 在中国市场份额超过 50%, 海外份额 ~28% (高工锂电 2026/04 调研)。
              与第二名的剪刀差主要来自一体化材料。我们识别出三条可能的反证机制 (见下方), 它们的成立条件直接定义 Bear 情景。</p>
          </section>
        </div>

        <aside class="business-side">
          <div class="shoulder-card">
            <h3>机制 — 证伪条件</h3>
            <p class="muted">每条机制下面有一条可证伪条件。如果成立, Base 切换到 Bear。</p>
            <ul style="font-size:12px;display:grid;gap:6px">
              <li><b>海外建厂确定性</b> — 折价 9%; 反证: <span style="color:var(--c-contradicts)">波兰 64%</span></li>
              <li><b>价格战反向抗性</b> — 反证: <span style="color:var(--c-contradicts)">0.55 元/Wh 出现</span></li>
              <li><b>储能 IRR 套利</b> — 反证: <span style="color:var(--c-contradicts)">美户储出货转负</span></li>
            </ul>
          </div>
          <div class="shoulder-card">
            <h3>关键跳转</h3>
            <ul style="display:grid;gap:6px;font-size:12px">
              <li><a href="#/forecast?case=catl" data-nav="forecast">→ 预测与情景</a></li>
              <li><a href="#/valuation?case=catl" data-nav="valuation">→ 价值判断</a></li>
              <li><a href="#/evidence?case=catl" data-nav="evidence">→ 证据中心</a></li>
              <li><a href="?workface=reader&case=catl" data-workface="reader">精确原文阅读 →</a></li>
            </ul>
          </div>
        </aside>
      </section>
    </div>
  `;
}

function renderAlphaBusiness() {
  const b = alphaBusiness;
  return `
    <div class="workbench is-unanswerable">
      <header class="wb-context" aria-label="研究上下文">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${alphaCompany.canonical_name} <small>${alphaCompany.ticker}</small></span>
          <span class="meta">${alphaCompany.industry}</span>
        </div>
        <div><span class="label">研究问题</span><span class="q-text">${alphaResearch.question}</span></div>
        <div class="state-block">
          <span class="stage-tag" style="color:var(--c-failed);border-color:var(--c-failed)"><span class="dot" style="background:var(--c-failed)"></span> 部分分析有效</span>
          <span class="meta">${alphaResearch.cutoff_at}</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">03 / 07 · 商业模式（部分）</p>
            <h1>Alphabet 的现金流形状与未解机制</h1>
            <p class="muted">估值不可生成; 我们仍可以展示已确认的部分经营分析。</p>
          </div>
        </header>
      </section>

      <div class="banner banner--blocked">
        <span class="b-icon">[阻塞]</span>
        <div>本案例在 SPEC §6.1 关键基线下不能完成财务桥接。
          我们因此停止于估值, 而把"已确认的部分分析"展示出来:
          搜索广告收入趋势、Cloud 增速的口径冲突、资本开支规模, 这些都不依赖未获得的 AI 推理毛利。</div>
      </div>

      ${businessModules(b)}

      ${mechanismsBlock(b)}

      ${counterEvidenceBlock(b)}

      <section class="page-overview">
        <div class="business-narrative">
          <section class="narrative-block">
            <h3><span class="ix">01</span> 已可观测的现金流形状</h3>
            <p>2025 全年披露营收 $350.0bn (10-K)。Cloud 业务在 Q1 2026 同比 +30% (管理层口径); 
              搜索广告在 Q1 2026 同比 +9.7%。这部分披露稳定, 不依赖未授权第三方数据。</p>
          </section>
          <section class="narrative-block">
            <h3><span class="ix">02</span> 未解机制</h3>
            <p>AI 资本回报拆分缺, 搜索份额监控无授权, 反垄断终裁未公布。
              这三者中的任意一项成立时, 现存 Base / Bull / Bear 都可能不再有效。</p>
          </section>
        </div>
      </section>
    </div>
  `;
}

function businessModules(b) {
  return `
    <section>
      <header class="section-head"><h2>业务单元（3 个）</h2><span class="sub">每个单元内嵌证据入口</span></header>
      <div class="module-grid">
        ${b.modules.map(m => `
          <article class="module-card">
            <div class="m-head">
              <span class="m-title">${m.label}</span>
              <span class="m-num">${b.modules.indexOf(m) + 1}/3</span>
            </div>
            <dl class="fact-chain" style="margin:0">
              <dt>收入来源</dt><dd>${m.revenue_sources.join(" · ")}</dd>
              <dt>主要客户</dt><dd>${m.customers.join(" · ")}</dd>
              <dt>单位经济</dt><dd>${m.unit_economics}</dd>
              <dt>资本占用</dt><dd>${m.capital}</dd>
              <dt>机制</dt><dd>${m.mechanism}</dd>
            </dl>
            ${m.kpi?.length ? `
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;border-top:1px solid var(--c-border);padding-top:8px">
                ${m.kpi.map(k => `
                  <div style="font-size:11.5px">
                    <span class="muted">${k.label}</span><br/>
                    <strong style="font-size:13px">${k.value}</strong>
                    <span class="muted" style="font-size:10.5px">${k.meta}</span>
                  </div>
                `).join("")}
              </div>
            ` : ""}
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function kpi(label, value, meta, kind, hasEvidence) {
  const metaClass = kind === "delta" ? "" : "";
  return `
    <div class="kpi">
      <span class="kpi-label">${label}</span>
      <span class="kpi-value">${value}</span>
      <span class="kpi-meta">${meta} ${hasEvidence ? '· <a href="?workface=reader&case=catl" data-workface="reader" style="border-bottom:1px dashed var(--c-border-strong)">查看依据</a>' : ''}</span>
    </div>
  `;
}

function mechanismsBlock(b) {
  return `
    <section>
      <header class="section-head"><h2>三条机制 · 每条都可以被证伪</h2><span class="sub">机制是"如果 ... 那么 ..."的假设, 不是结论</span></header>
      <div class="mechanism-list">
        ${b.mechanisms.map(m => `
          <article class="mechanism">
            <div>
              <div class="m-title">${m.title}</div>
              <div class="m-falsifier">反证 · ${m.falsifier}</div>
            </div>
            <div class="m-levers">
              ${m.levers.map(l => `<span>· ${l}</span>`).join("")}
            </div>
            <div class="m-actions">
              <a class="btn" data-open="reader">查看依据</a>
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function counterEvidenceBlock(b) {
  return `
    <section class="counterevidence-strip">
      <h4>↯ 反证 — 必须与支持证据共同出现 (SPEC §6.3)</h4>
      <ul>
        ${b.counterevidence.map(c => `<li><span>${c}</span></li>`).join("")}
      </ul>
    </section>
  `;
}
