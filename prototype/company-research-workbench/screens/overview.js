/**
 * Page 2: 研究概览 (SPEC §9)
 * 内容: 研究问题、公司/证券、资料截止、可回答性、运行阶段、
 *       三条关键发现、最大缺口、下一验证事件。结果优先, 完整日志折叠。
 */
import {
  catlResearch, catlCompany, catlBusiness, catlCriticalInputs, catlVersions,
  alphaResearch, alphaCompany, alphaBusiness, alphaCriticalInputs, alphaVersions,
  criticalInputsFor,
} from "../fixtures.js";

const STAGE_KEY = ["collecting_sources","analyzing_company","building_forecast","generating_report","needs_input","completed"];
const STAGE_LABEL = {
  collecting_sources: "采集资料",
  analyzing_company: "分析公司",
  building_forecast: "建模预测",
  generating_report: "撰写初稿",
  needs_input: "关键输入",
  completed: "冻结保存",
};

export function renderOverview(state) {
  return state.caseId === "alphabet"
    ? renderAlphaOverview()
    : renderCatlOverview();
}

function renderCatlOverview() {
  const r = catlResearch;
  const stops = renderStops(catlResearch.stages);
  return `
    <div class="workbench${r.answerability === "not_answerable" ? " is-unanswerable" : ""}">
      ${renderContextBar(catlCompany, catlResearch)}
      ${stops}

      <section class="headline-band">
        <div>
          <p class="eyebrow">研究结论 · 摘要</p>
          <h1 class="headline-h1">海外扩张能让公司 FY27 净利率回到 12% 以上, 但路径存在三条不确定情景</h1>
          <p class="lead">在 Base 假设下, ASP 修复至 0.61 元/Wh 与海外占比 45% 是主要拉动力;Bull 与 Bear 由 EU CBAM 是否提前至 2026 全部生效、价格战是否扩大二线产能划开。三情景下保留同样三处反证。</p>
        </div>
        <div class="answer-band">
          <span class="answer-pill" data-state="${r.answerability}">${answerabilityLabel(r.answerability)}</span>
          <a class="btn" href="?workface=inputs&case=catl" data-workface="inputs">关键输入确认 →</a>
        </div>
      </section>

      <div class="page-overview">
        <div class="business-narrative">
          <section>
            <header class="section-head">
              <h2>三条关键发现</h2>
              <span class="sub">每项均链接到证据中心 / 财务桥接 / 备忘录</span>
            </header>
            <div class="findings">
              ${finding(1, "海外销售占比已突破 38%, 单 GWh 不含税收入 ¥6.4 亿维持", ["证据中心: 2025 年报 P.18", "财务桥接 FY26 行 1"])}
              ${finding(2, "现金循环周期 FY25 改善 11 天, 营运资本压力减轻, 是 FY27-28 FCFF 上行的关键", ["证据中心: 2025 年报 P.43", "备忘录 §3 桥接"])}
              ${finding(3, "波兰工厂 Q4 实际产能 64% — 海外确定性折价 9% 已被识别, 是 Bull/Bear 分叉点", ["证据中心: 高工锂电 2026/04", "反证 §5"])}
            </div>
          </section>

          <section class="oversight-row">
            <article class="oversight-card urgent">
              <h4 class="o-title">⚑ 最大缺口 · 阻塞</h4>
              <ul class="o-list">
                <li><span>FY27 ASP 修复路径</span><span class="o-action">AI 假设, 待确认</span></li>
                <li><span>波兰工厂产能利用率反证</span><span class="o-action">来自外部, 尚未决策</span></li>
                <li><span>FY27 户用储能出货</span><span class="o-action">无授权, 已接受缺口</span></li>
              </ul>
              <a class="btn" href="?workface=inputs&case=catl" data-workface="inputs" style="margin-top:8px">去处理</a>
            </article>
            <article class="oversight-card">
              <h4 class="o-title">↪ 下一验证事件</h4>
              <ul class="o-list">
                <li><span>2026Q2 财报（2026-08-30）</span><span class="o-action">海外销售口径</span></li>
                <li><span>2026H2 储能出海订单</span><span class="o-action">客户结构</span></li>
                <li><span>欧盟 CBAM 实施细则</span><span class="o-action">Q4 2026</span></li>
              </ul>
            </article>
          </section>

          <section>
            <header class="section-head">
              <h2>当前进度 · 阶段时间线</h2>
              <span class="sub">只显示研究阶段, 不暴露 worker / artifact</span>
            </header>
            <ul class="modules-strip">
              ${catlStages()}
            </ul>
          </section>
        </div>

        <aside class="shoulder">
          <article class="shoulder-card">
            <h3>公司 / 证券</h3>
            <div class="r-row"><span class="r-label">公司</span><span class="r-value">${catlCompany.canonical_name}</span></div>
            <div class="r-row"><span class="r-label">证券</span><span class="r-value">${catlCompany.ticker}</span></div>
            <div class="r-row"><span class="r-label">交易所 / 货币</span><span class="r-value">${catlCompany.exchange} · ${catlCompany.trading_currency}</span></div>
            <div class="r-row"><span class="r-label">行业</span><span class="r-value">${catlCompany.industry}</span></div>
          </article>

          <article class="shoulder-card">
            <h3>资料基础</h3>
            <div class="r-row"><span class="r-label">基础截止</span><span class="r-value">${r.cutoff_at}</span></div>
            <div class="r-row"><span class="r-label">基础窗口</span><span class="r-value">${r.context_meta.basis_window}</span></div>
            <div class="r-row"><span class="r-label">活跃资料 / 已复核</span><span class="r-value">${r.context_meta.sources_active} / ${r.context_meta.sources_reviewed}</span></div>
            <div class="r-row"><span class="r-label">基础 ID</span><span class="r-value"><code>${r.historical_basis_id}</code></span></div>
            <div class="r-row"><span class="r-label">策略 / 模型</span><span class="r-value">${r.strategy_version} / ${r.model_version}</span></div>
          </article>

          <article class="shoulder-card">
            <h3>回答状态</h3>
            <div class="r-row">
              <span class="r-label">回答能力</span>
              <span class="r-value" style="color:var(--c-reviewed)">${answerabilityLabel(r.answerability)}</span>
            </div>
            <div class="r-row"><span class="r-label">已确认事实</span><span class="r-value">${r.context_meta.evidence_facts_confirmed}</span></div>
            <div class="r-row"><span class="r-label">已决策输入</span><span class="r-value">${getDecided(catlCriticalInputs)} / ${catlCriticalInputs.length}</span></div>
            <div class="r-row"><span class="r-label">未冻结版本</span><span class="r-value">${catlVersions.filter(v => !v.is_frozen).length} 个</span></div>
          </article>

          <article class="shoulder-card">
            <h3>快捷跳转</h3>
            <ul style="display:grid;gap:6px">
              <li><a href="#/business?case=catl" data-nav="business">→ 商业模式 (单 GWh 单位经济)</a></li>
              <li><a href="#/forecast?case=catl" data-nav="forecast">→ 财务桥接 + 三情景</a></li>
              <li><a href="#/valuation?case=catl" data-nav="valuation">→ 价值区间 / 反向 DCF</a></li>
              <li><a href="#/evidence?case=catl" data-nav="evidence">→ 证据中心</a></li>
              <li><a href="#/versions?case=catl" data-nav="versions">→ 备忘录 / 版本</a></li>
            </ul>
          </article>
        </aside>
      </div>
    </div>
  `;
}

function renderAlphaOverview() {
  const r = alphaResearch;
  const blockers = r.blockers || [];
  return `
    <div class="workbench is-unanswerable">
      ${renderContextBar(alphaCompany, alphaResearch)}

      <div class="banner banner--blocked">
        <span class="b-icon">[阻塞]</span>
        <div>
          <strong style="font-size:13.5px">研究不可回答：</strong>
          按 SPEC §8 与 §11, 关键经营基线（AI 推理单位毛利）缺失时不进行估值。
          系统不展示目标价值、回报区间、买卖命令。当前展示可用于研究债务管理。
          <br/>
          <span class="muted" style="font-size:12px">
            ${blockers.length} 个当前阻塞项, 见右侧与证据中心。
            <a class="link-to" href="?workface=inputs&case=alphabet" data-workface="inputs" style="margin-left:12px">打开关键输入确认 →</a>
          </span>
        </div>
      </div>

      <section class="headline-band">
        <div>
          <p class="eyebrow">研究结论 · 摘要</p>
          <h1 class="headline-h1">当前不可判断</h1>
          <p class="lead">部分经营分析仍然有效，但估值门禁不满足。我们仍可保存为 not_answerable 版本, 作为研究债务管理的研究底稿。</p>
        </div>
        <div class="answer-band">
          <span class="answer-pill" data-state="${r.answerability}">${answerabilityLabel(r.answerability)}</span>
        </div>
      </section>

      <div class="page-overview">
        <div class="business-narrative">
          <section>
            <header class="section-head"><h2>已识别机制（可获得的事实）</h2><span class="sub">每项均并列保留管理层 / 一致两条口径</span></header>
            <div class="findings">
              ${finding(1, "Q1 2026 搜索广告 +9.7% YoY, 管理层归因 AI Overview 但份额第三方监控不可获得", ["10-Q Q1 2026 P.6", "Earnings call transcript"])}
              ${finding(2, "Cloud Q1 2026 增速在管理层 +30% 与一致 +24% 间存在分歧, 系统并列保留", ["Bloomberg 一致 2026-06-29", "Earnings call"])}
              ${finding(3, "FY26 资本开支指引 $75bn 但未拆分到 Cloud / Search, 无法验证回报机制", ["Earnings call", "10-Q 季度披露"])}
            </div>
          </section>

          <section class="oversight-row">
            <article class="oversight-card urgent">
              <h4 class="o-title">⚑ 最大缺口 · 阻塞估值</h4>
              <ul class="o-list">
                <li><span>AI 推理单位毛利</span><span class="o-action">披露缺失, 阻塞财务桥接</span></li>
                <li><span>搜索份额监控</span><span class="o-action">无授权第三方数据</span></li>
                <li><span>反垄断最终裁决</span><span class="o-action">未公布</span></li>
                <li><span>Cloud 增速口径</span><span class="o-action">需要用户选择</span></li>
              </ul>
              <a class="btn" href="?workface=inputs&case=alphabet" data-workface="inputs" style="margin-top:8px">去处理</a>
            </article>
            <article class="oversight-card">
              <h4 class="o-title">↪ 何时升级可回答</h4>
              <ul class="o-list">
                <li><span>Q3 2026 财报（10月）</span><span class="o-action">AI 推理口径</span></li>
                <li><span>SimilarWeb / Statcounter 授权</span><span class="o-action">采购 / 申请</span></li>
                <li><span>反垄断裁决公开</span><span class="o-action">司法部进度</span></li>
              </ul>
            </article>
          </section>
        </div>

        <aside class="shoulder">
          <article class="unanswerable-card">
            <h3>为什么不估值</h3>
            <p>SPEC §8 明确禁止在关键经营基线、市场输入或资本结构缺失时生成价值区间。我们因此停止于估值, 而把现有材料保存为 not_answerable 版本用于研究债务管理。</p>
            <p>研究不展示任何目标价值或回报区间。</p>
          </article>
          <article class="shoulder-card">
            <h3>公司 / 证券</h3>
            <div class="r-row"><span class="r-label">公司</span><span class="r-value">${alphaCompany.canonical_name}</span></div>
            <div class="r-row"><span class="r-label">证券</span><span class="r-value">${alphaCompany.ticker} Class A</span></div>
            <div class="r-row"><span class="r-label">交易所 / 货币</span><span class="r-value">${alphaCompany.exchange} · ${alphaCompany.trading_currency}</span></div>
          </article>
          <article class="shoulder-card">
            <h3>基础状态</h3>
            <div class="r-row"><span class="r-label">回答能力</span><span class="r-value" style="color:var(--c-failed)">不可回答</span></div>
            <div class="r-row"><span class="r-label">截止</span><span class="r-value">${r.cutoff_at}</span></div>
            <div class="r-row"><span class="r-label">开放缺口</span><span class="r-value">${r.context_meta.gaps_open}</span></div>
            <div class="r-row"><span class="r-label">已确认事实</span><span class="r-value">${r.context_meta.evidence_facts_confirmed}</span></div>
          </article>
        </aside>
      </div>
    </div>
  `;
}

function finding(num, text, links) {
  return `
    <article class="finding-card">
      <span class="f-num">发现 ${num}</span>
      <p class="f-title">${text}</p>
      <div class="f-evidence">
        ${links.map(l => `<a data-open="reader" href="#evidence">${l}</a>`).join(" · ")}
      </div>
    </article>
  `;
}

function renderContextBar(company, research) {
  const stage = STAGE_LABEL[research.stage] || "—";
  return `
    <header class="wb-context" aria-label="研究上下文">
      <div>
        <span class="label">研究对象</span>
        <span class="value">${company.canonical_name} <small>${company.ticker}</small></span>
        <span class="meta">${company.exchange} · ${company.trading_currency}</span>
      </div>
      <div class="question-block">
        <div>
          <span class="label">研究问题</span>
          <span class="q-text">${research.question}</span>
        </div>
      </div>
      <div class="state-block">
        <span class="stage-tag"><span class="dot"></span> 阶段 · ${stage}</span>
        <div style="display:flex;flex-direction:column">
          <span class="label">资料截止</span>
          <span class="value" style="font-size:13px">${research.cutoff_at}</span>
        </div>
        <div style="display:flex;flex-direction:column">
          <span class="label">基础</span>
          <span class="value" style="font-size:12px"><code>${research.historical_basis_id}</code></span>
        </div>
      </div>
    </header>
  `;
}

function renderStops(stages) {
  // 行为: queued → collecting → analyzing → building → generating → completed (+ needs_input 分支)
  const completed = stages.filter(s => s.state === "completed").length;
  const total = stages.length;
  const nowLabel = stages.find(s => s.state === "now")?.label || "—";
  const blocked = stages.some(s => s.state === "blocked");
  return `
    <div class="run-track ${blocked ? "is-blocked" : completed === total ? "is-completed" : ""}">
      <span style="font-weight:600">运行轨道</span>
      <div class="track"><div class="fill" style="width:${(completed/total*100).toFixed(0)}%"></div></div>
      <ul class="stages">
        ${stages.map(s => `
          <li class="${s.state === 'completed' ? 'is-done' : ''} ${s.state === 'now' ? 'is-now' : ''}">
            <span>${s.state === 'completed' ? '✓' : s.state === 'now' ? '◆' : s.state === 'blocked' ? '✕' : '○'}</span>
            ${s.label}
          </li>
        `).join("")}
      </ul>
    </div>
  `;
}

function catlStages() {
  // 9 个模块（按 SPEC §9 顺序映射）
  const modules = [
    { key: "overview", label: "研究概览", state: "ready" },
    { key: "business_map", label: "商业模式", state: "ready" },
    { key: "operating_drivers", label: "经营驱动", state: "ready" },
    { key: "evidence_and_gaps", label: "证据与缺口", state: "needs_review" },
    { key: "industry_competition_regulation", label: "行业 / 竞争 / 监管", state: "ready" },
    { key: "financials_cash_flow_capital_allocation", label: "财务 / 现金流 / 资本分配", state: "ready" },
    { key: "scenarios_valuation_implied_expectations", label: "情景 / 估值 / 隐含条件", state: "ready" },
    { key: "counterevidence_risks_next_checks", label: "反证 / 风险 / 下一验证", state: "ready" },
    { key: "versions_changes_memo", label: "版本 / 变更 / 备忘录", state: "preparing" },
  ];
  return modules.map((m, i) => `
    <li>
      <span class="m-num">${(i + 1).toString().padStart(2, "0")}</span>
      <a href="/${m.key === 'overview' ? 'overview' : m.key === 'business_map' ? 'business' : m.key === 'operating_drivers' ? 'forecast' : m.key === 'evidence_and_gaps' ? 'evidence' : m.key === 'scenarios_valuation_implied_expectations' ? 'valuation' : m.key === 'versions_changes_memo' ? 'versions' : 'forecast'}?case=catl" data-nav="${m.key === 'overview' ? 'overview' : m.key === 'business_map' ? 'business' : m.key === 'operating_drivers' ? 'forecast' : m.key === 'evidence_and_gaps' ? 'evidence' : m.key === 'scenarios_valuation_implied_expectations' ? 'valuation' : m.key === 'versions_changes_memo' ? 'versions' : 'forecast'}" style="color:var(--c-ink)">${m.label}</a>
      <span class="m-state" data-state="${m.state}">${stateLabel(m.state)}</span>
      <a href="#" data-open="reader" style="font-size:11px">查看依据</a>
    </li>
  `).join("");
}

function stateLabel(state) {
  return ({
    ready: "✓ 完成",
    needs_review: "⚠ 待复核",
    preparing: "进行中",
    blocked: "✕ 阻塞",
    not_started: "○ 待开始",
  })[state] || state;
}

function answerabilityLabel(a) {
  return ({ answerable: "可回答", partially_answerable: "部分可回答", not_answerable: "不可回答" })[a] || a;
}

function getDecided(inputs) {
  return inputs.filter(i => i.decisions && i.decisions.length > 0).length;
}
