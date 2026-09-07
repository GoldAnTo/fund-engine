/**
 * Page 5: 价值判断 (SPEC §9)
 * 内容: 价格/汇率/股本/权利的 as-of、价值区间、反向估值、敏感性、
 *       要求回报比较、反证、估值阻塞。
 * 关键跳转: 打开市场快照、模型输入与相关缺口。
 */
import {
  catlCompany, catlResearch, catlValuation,
  alphaCompany, alphaResearch,
} from "../fixtures.js";

export function renderValuation(state) {
  return state.caseId === "alphabet"
    ? renderAlphaValuation()
    : renderCatlValuation();
}

function renderCatlValuation() {
  const v = catlValuation;
  return `
    <div class="workbench">
      <header class="wb-context">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${catlCompany.canonical_name} <small>${catlCompany.ticker}</small></span>
        </div>
        <div><span class="label">研究问题</span><span class="q-text">${catlResearch.question}</span></div>
        <div class="state-block">
          <span class="stage-tag"><span class="dot"></span> 价值判断</span>
          <span class="meta">${v.asof.cutoff_at}</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">05 / 07 · 价值判断</p>
            <h1>当前价格所隐含的条件, 与三情景给定的范围</h1>
            <p class="muted">价格、汇率、股本与权利的时点必须与估值一致; 任何错位都不进入数字层。</p>
          </div>
          <div class="page-actions">
            <a class="btn" href="#/forecast?case=catl" data-nav="forecast">← 预测</a>
            <a class="btn" href="#/evidence?case=catl" data-nav="evidence">证据中心</a>
          </div>
        </header>
      </section>

      <section>
        <header class="section-head"><h2>市场快照 · as-of</h2><span class="sub">${v.asof.cutoff_at}</span></header>
        <div class="snapshot-asof">
          <div>
            <span class="snap-label">当前价格</span>
            <span class="snap-value">¥${v.asof.price.toFixed(2)}</span>
            <span class="snap-meta">300750.SZ · 深交所</span>
          </div>
          <div>
            <span class="snap-label">FX</span>
            <span class="snap-value">${v.asof.fx.toFixed(2)}</span>
            <span class="snap-meta">PBoC 中间价 USD/CNY</span>
          </div>
          <div>
            <span class="snap-label">市值 (亿元)</span>
            <span class="snap-value">${v.asof.market_cap.toLocaleString()}</span>
            <span class="snap-meta">${v.asof.shares_diluted.toFixed(2)} 亿股 · 摊薄</span>
          </div>
          <div>
            <span class="snap-label">要求回报</span>
            <span class="snap-value">${(v.required_return * 100).toFixed(1)}%</span>
            <span class="snap-meta">默认策略 v2025.4 (本土 + ERP 5.5%)</span>
          </div>
        </div>
      </section>

      <section>
        <header class="section-head"><h2>三情景企业价值与每股区间</h2><span class="sub">Base / Bull / Bear 分别对应不同机制, 不是同组数字</span></header>
        <div class="scenario-rv">
          ${Object.entries(v.range).filter(([k]) => ["base","bull","bear"].includes(k)).map(([k, range]) => `
            <article class="rv-cell" data-id="${k}">
              <span class="label">${rangeLabel(k)}</span>
              <span class="ev">¥${range.lo}–${range.hi}</span>
              <span class="rr ${currentPriceSide(v.range.current_price, range)}">
                当前价 ¥${v.range.current_price} → ${sideText(v.range.current_price, range)}
              </span>
            </article>
          `).join("")}
        </div>
      </section>

      <section>
        <header class="section-head"><h2>反向 DCF · 当前价格所隐含的条件</h2><span class="sub">市场隐含 vs 我们的分析</span></header>
        <div class="reverse-implied">
          <div>
            <h4>${v.reverse.narrative}</h4>
            <dl class="target-grid">
              <dt>驱动</dt><dd>${v.reverse.driver_key}</dd>
              <dt>隐含值</dt><dd>${v.reverse.implied_value}</dd>
              <dt>达成残差</dt><dd>${v.reverse.achieved_residual}</dd>
              <dt>迭代</dt><dd>${v.reverse.iteration_count} 次</dd>
            </dl>
          </div>
          <div>
            <p style="font-size:12px">当前价格 ¥${v.range.current_price} 已经对海外扩张的乐观情景部分定价。
              Bear 情景下定价仍高于隐含, 说明市场没有对 CBAM 提前触发定价 (反证出处: §5)。</p>
            <p class="muted" style="font-size:11px">算法 <code>valuation_set.v1</code> · 模型版本 v2025.4-r3</p>
          </div>
        </div>
      </section>

      <section>
        <header class="section-head"><h2>敏感性 · WACC × 永续增速</h2><span class="sub">¥/股</span></header>
        <table class="sensitivity-table">
          <thead>
            <tr><th></th><th>g=1.5%</th><th>g=2.0%</th><th>g=2.5%</th><th>g=3.0%</th></tr>
          </thead>
          <tbody>
            ${rows(v.sensitivity)}
          </tbody>
        </table>
        <p class="muted" style="font-size:11px;margin-top:6px">蓝绿色 ≥ 当前价 ¥${v.range.current_price} · 红橙色 < 当前价 · 粗体 = Base 中心</p>
      </section>

      <section class="valuation-blockers">
        <h4>⚑ 反证 / 阻塞</h4>
        <ul>
          <li><span>波兰工厂 Q4 产能 64% (反证)</span><a class="btn" href="?workface=reader&case=catl" data-workface="reader">查看原文 →</a></li>
          <li><span>国内 ASP 现货 0.56 元/Wh</span><a class="btn" href="?workface=reader&case=catl" data-workface="reader">查看原文 →</a></li>
          <li><span>FY27 户用储能出货 (接受缺口)</span><a class="btn" href="?workface=inputs&case=catl" data-workface="inputs">去处理 →</a></li>
        </ul>
      </section>
    </div>
  `;
}

function renderAlphaValuation() {
  return `
    <div class="workbench is-unanswerable">
      <header class="wb-context">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${alphaCompany.canonical_name} <small>${alphaCompany.ticker}</small></span>
        </div>
        <div><span class="label">研究问题</span><span class="q-text">${alphaResearch.question}</span></div>
        <div class="state-block">
          <span class="stage-tag" style="color:var(--c-failed);border-color:var(--c-failed)"><span class="dot" style="background:var(--c-failed)"></span> 估值阻塞</span>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">05 / 07 · 价值判断（不导出）</p>
            <h1>当前不可判断</h1>
            <p class="muted">按 SPEC §8 价值判断边界, 我们不输出任何目标价值、回报区间、买卖命令。我们也拒绝反向 DCF, 因为它会对一个缺失的事实给出数字外观。</p>
          </div>
        </header>
      </section>

      <div class="unanswerable-card">
        <h3>⚑ 不展示以下数字</h3>
        <ul style="margin:0 0 0 16px;list-style:disc;font-size:13px;color:var(--c-ink-2)">
          <li>三情景企业价值区间 (按 SPEC §8 明令禁止)</li>
          <li>反向 DCF 隐含条件 (不能用 AI 补全关键缺口)</li>
          <li>WACC × 永续增速 敏感性表 (依赖情景才能搭)</li>
          <li>要求回报比较 (依赖市值-价值差)</li>
        </ul>
      </div>

      <section>
        <header class="section-head"><h2>已确认的部分事实 (摘要)</h2></header>
        <table class="bridge-table">
          <thead>
            <tr><th>科目</th><th>FY25</th><th>FY26 Q1</th></tr>
          </thead>
          <tbody>
            <tr><td>营收</td><td class="num">$350.0 bn</td><td class="num">—</td></tr>
            <tr><td>搜索广告</td><td class="num">—</td><td class="num">$48.7 bn (+9.7% YoY)</td></tr>
            <tr><td>Google Cloud</td><td class="num">—</td><td class="num">$12.3 bn (+30% 管理层 / +24% 一致)</td></tr>
            <tr><td>FY26 capex</td><td class="num">—</td><td class="num">~$75 bn (管理层指引)</td></tr>
          </tbody>
        </table>
      </section>

      <section>
        <header class="section-head"><h2>何时升级可回答</h2></header>
        <ul style="font-size:13px;line-height:1.6;max-width:78ch">
          <li>Q3 2026 财报 (2026-10) 提供 AI 推理口径 → 重新进入预测</li>
          <li>SimilarWeb / Statcounter 第三方授权 → 重新建模搜索份额</li>
          <li>反垄断最终裁决公开 → 重新设定情景边界</li>
        </ul>
        <p style="margin-top:12px"><a class="btn" href="?workface=inputs&case=alphabet" data-workface="inputs">打开关键输入确认 →</a></p>
      </section>
    </div>
  `;
}

function rows(matrix) {
  const current = catlValuation.range.current_price;
  const waccList = [...new Set(matrix.map(r => r.wacc))].sort();
  const gList = [0.015, 0.020, 0.025, 0.030];
  return waccList.map(w => `
    <tr>
      <td style="text-align:left"><b>WACC ${(w*100).toFixed(1)}%</b></td>
      ${gList.map(g => {
        const cell = matrix.find(r => r.wacc === w && Math.abs(r.g - g) < 0.0005);
        if (!cell) return "<td>—</td>";
        const high = cell.val >= current;
        const near = Math.abs(cell.val - current) < 5;
        return `<td class="${high ? 'fl-high' : 'fl-low'} ${near ? 'fl-base' : ''}">¥${cell.val}</td>`;
      }).join("")}
    </tr>
  `).join("");
}

function rangeLabel(k) {
  return ({ base: "Base · 主情景", bull: "Bull · 乐观情景", bear: "Bear · 悲观情景" })[k] || k;
}

function currentPriceSide(price, r) {
  if (price >= r.lo && price <= r.hi) return "";
  if (price < r.lo) return "pos";
  return "neg";
}

function sideText(price, r) {
  if (price >= r.lo && price <= r.hi) return "<span style='color:var(--c-reviewed)'>在区间内</span>";
  if (price < r.lo) return "<span style='color:var(--c-reviewed)'>高于价值上限</span>";
  return "<span style='color:var(--c-contradicts)'>低于价值下限</span>";
}
