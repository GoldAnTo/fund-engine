const RESEARCH_QUESTION = '以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？';

const factors = [
  {
    id: 'search-ads',
    name: '搜索广告经济性',
    mechanism: '查询意图、分发入口与广告匹配共同决定单次查询的商业价值。',
    observations: ['搜索收入仍是现金流核心', 'AI 摘要改变结果页交互，但商业化节奏尚未稳定'],
    support: '高商业意图查询保持韧性，流量获取成本占比稳定。',
    counter: 'AI 答案减少外链点击，部分查询可能弱化广告加载与点击。',
    alternativeExplanation: '收入韧性也可能来自价格提升，而非用户价值或查询量改善。',
    unknown: 'AI 搜索形态成熟后，每次查询的净收入能否维持。',
    nextValidation: '2026 Q3 业绩，核对付费点击、单次点击成本与流量获取成本。',
    financialImpact: '每 1% 搜索收入差异，约影响集团营业利润率 20–30bp。',
    falsifier: '连续两个季度搜索收入增速低于查询量增速，且流量获取成本率上升。',
    whyKey: '决定现金流底盘是否足以支撑再投资',
    state: '韧性仍在，AI 形态的单位变现待验证',
    metrics: metricSet('搜索收入增速', '12.5', '12–14', '11.8', '9.6', '13.2', '%'),
  },
  {
    id: 'cloud',
    name: 'Cloud 单位经济性',
    mechanism: '工作负载迁移、利用率与产品组合决定云业务增量毛利。',
    observations: ['收入增速重新加快', '营业利润率随规模和高附加值服务改善'],
    support: '积压合同与 AI 基础设施需求提供中期可见度。',
    counter: '供给扩张和折旧增加可能延后利润释放。',
    alternativeExplanation: '利润率改善可能主要来自费用节制，而非单位经济性增强。',
    unknown: 'AI 工作负载的全周期回报是否高于传统云工作负载。',
    nextValidation: '2026 Q3 业绩，核对 Cloud 营业利润率与剩余履约义务。',
    financialImpact: 'Cloud 利润率每变化 200bp，约影响集团营业利润 1.0%。',
    falsifier: '收入维持 20% 以上增长时，营业利润率连续两个季度下降。',
    whyKey: '决定第二利润池能否从增长故事转为现金贡献',
    state: '规模效应显现，新增算力的回报周期仍不清楚',
    metrics: metricSet('Cloud 营业利润率', '20.7', '20–22', '21.6', '24.0', '22.8', '%'),
  },
  {
    id: 'ai-capital',
    name: 'AI 资本效率',
    mechanism: '资本开支先形成算力供给，再经内部效率、云收入和产品变现转为现金回报。',
    observations: ['资本开支显著上行', '折旧与能源约束将滞后进入利润表'],
    support: '自研芯片、数据中心规模与全产品分发提供较高利用率基础。',
    counter: '供给竞赛可能使投入先于可付费需求。',
    alternativeExplanation: '短期收入改善可能来自算力稀缺，而非长期资本效率。',
    unknown: '新增资本开支的稳态收入资本比与折旧压力。',
    nextValidation: '2026 Q3 业绩，核对资本开支指引与新增折旧口径。',
    financialImpact: '资本开支强度每高 2pct，在收入假设不变时压低自由现金流率约 2pct。',
    falsifier: '资本开支强度上升超过 5pct，同时 AI 相关收入增量连续两期低于内部假设。',
    whyKey: '决定 AI 优势最终增厚还是稀释每股现金流',
    state: '投入确定，回收路径跨业务且尚未闭合',
    metrics: metricSet('资本开支 / 收入', '18.4', '20–22', '20.6', '23.5', '21.4', '%'),
  },
  {
    id: 'competition',
    name: '竞争与监管',
    mechanism: '分发入口、默认协议和监管补救措施共同影响获客成本与收入留存。',
    observations: ['搜索入口竞争加剧', '关键司法程序仍可能改变默认分发安排'],
    support: '品牌习惯、产品覆盖和默认之外的自然使用仍具黏性。',
    counter: '补救措施可能提高分发成本，或降低默认入口份额。',
    alternativeExplanation: '份额稳定可能是竞争产品仍处早期，而非结构性壁垒。',
    unknown: '最终补救措施对默认协议、数据使用与商业模式的约束。',
    nextValidation: '2026-10-15，更新美国搜索案补救措施与浏览器分发协议。',
    financialImpact: '流量获取成本率每上升 1pct，约压低集团营业利润率 70bp。',
    falsifier: '非默认入口使用率明显下滑，且分发成本率在补救措施后持续上升。',
    whyKey: '决定搜索优势可保留多少，以及保留它需要付出多少成本',
    state: '经营影响尚未落地，尾部风险不可忽略',
    metrics: metricSet('流量获取成本率', '20.6', '20–21', '20.8', '22.4', '21.0', '%'),
  },
];

export function renderWorkbench(root, { navigate, params }) {
  const securityCode = params.get('security') === 'GOOG' ? 'GOOG' : 'GOOGL';
  const security = securityCode === 'GOOG' ? 'GOOG Class C' : 'GOOGL Class A';
  const variant = ['A', 'B', 'C'].includes(params.get('variant')) ? params.get('variant') : 'A';

  root.innerHTML = `
    <main class="workbench-screen" id="main-content" data-workbench-variant="${variant}">
      ${sharedContext(securityCode, security)}
      ${variant === 'A' ? renderVariantA() : variant === 'B' ? renderVariantB() : renderVariantC()}
    </main>
    ${renderPrototypeSwitcher(securityCode, variant)}
  `;

  root.addEventListener('click', handleFactorToggle);
  root.addEventListener('click', handleVariantClick);
  document.addEventListener('keydown', handleVariantKeydown);
  return () => {
    root.removeEventListener('click', handleFactorToggle);
    root.removeEventListener('click', handleVariantClick);
    document.removeEventListener('keydown', handleVariantKeydown);
  };

  function handleFactorToggle(event) {
    const button = event.target.closest('[aria-controls^="factor-detail-"]');
    if (!button || !root.contains(button)) return;
    const factor = factors.find(({ id }) => button.getAttribute('aria-controls') === `factor-detail-${id}`);
    if (!factor) return;

    const shouldOpen = button.getAttribute('aria-expanded') !== 'true';
    root.querySelectorAll('[aria-controls^="factor-detail-"]').forEach((control) => {
      control.setAttribute('aria-expanded', 'false');
      control.querySelector('.factor-toggle').textContent = '＋';
    });
    root.querySelectorAll('[data-testid="factor-detail"]').forEach((panel) => {
      panel.hidden = true;
      panel.closest('.factor-detail-row').hidden = true;
    });

    if (shouldOpen) {
      button.setAttribute('aria-expanded', 'true');
      button.querySelector('.factor-toggle').textContent = '−';
      const panel = root.querySelector(`#factor-detail-${factor.id}`);
      panel.hidden = false;
      panel.closest('.factor-detail-row').hidden = false;
    }
  }

  function handleVariantClick(event) {
    const button = event.target.closest('[data-variant-step]');
    if (!button || !root.contains(button)) return;
    cycleVariant(button.dataset.variantStep === 'next' ? 1 : -1);
  }

  function handleVariantKeydown(event) {
    if (
      !['ArrowLeft', 'ArrowRight'].includes(event.key)
      || event.altKey
      || event.ctrlKey
      || event.metaKey
      || event.shiftKey
      || isTextEntry(event.target)
      || !hasVariantKeyboardScope(event.target)
    ) return;
    event.preventDefault();
    cycleVariant(event.key === 'ArrowRight' ? 1 : -1, { restoreSwitcherFocus: true });
  }

  function cycleVariant(direction, { restoreSwitcherFocus = false } = {}) {
    const variants = ['A', 'B', 'C'];
    const nextIndex = (variants.indexOf(variant) + direction + variants.length) % variants.length;
    navigate({ screen: 'workbench', security: securityCode, variant: variants[nextIndex] });
    if (restoreSwitcherFocus) {
      root.querySelector('[data-prototype-switcher] a[aria-current="page"]')?.focus({ preventScroll: true });
    }
  }
}

function renderPrototypeSwitcher(securityCode, variant) {
  const labels = { A: '判断优先', B: '模型优先', C: 'PM 备忘录' };
  return `
    <nav class="prototype-switcher" data-prototype-switcher aria-label="PROTOTYPE 方案切换器">
      <span class="prototype-label">PROTOTYPE</span>
      <button type="button" data-variant-step="previous" aria-label="上一个原型方案">←</button>
      <div class="prototype-options">
        ${Object.entries(labels).map(([key, label]) => `<a href="?screen=workbench&security=${securityCode}&variant=${key}" ${key === variant ? 'aria-current="page"' : ''}>${key} ${label}</a>`).join('')}
      </div>
      <button type="button" data-variant-step="next" aria-label="下一个原型方案">→</button>
    </nav>
  `;
}

function isTextEntry(target) {
  if (!(target instanceof Element)) return false;
  if (target.matches('input, textarea, select')) return true;
  const editable = target.closest('[contenteditable]');
  return editable !== null && editable.getAttribute('contenteditable') !== 'false';
}

function hasVariantKeyboardScope(target) {
  if (!(target instanceof Element)) return false;
  return target.closest('[data-prototype-switcher]') !== null;
}

function sharedContext(securityCode, security) {
  return `
    <a class="back-link" href="?screen=setup&security=${securityCode}">
      <span aria-hidden="true">←</span> 返回研究设置
    </a>
    <ol class="workbench-steps" aria-label="研究步骤">
      <li><span>01</span>选择主体</li>
      <li><span>02</span>定义问题</li>
      <li class="is-current" aria-current="step"><span>03</span>维护判断</li>
    </ol>
    <header class="workbench-context">
      <div class="workbench-identity">
        <span class="identity-mark workbench-mark" aria-hidden="true">A</span>
        <div>
          <p class="eyebrow">研究工作台 · 公司与特定证券</p>
          <h1>Alphabet</h1>
          <p>${security} <span>NASDAQ · USD</span></p>
        </div>
      </div>
      <div class="workbench-question">
        <span>研究问题</span>
        <strong>${RESEARCH_QUESTION}</strong>
        <small>研究视角 · 3–5 年</small>
      </div>
      <dl class="context-ledger">
        <div><dt>当前价格 <em>模拟数据</em></dt><dd>$201.42</dd><small>价格时间 2026-08-19 16:00 ET</small></div>
        <div data-evidence-cutoff="2026-08-19"><dt>证据截止</dt><dd>2026-08-19</dd><small>已冻结研究边界</small></div>
        <div><dt>判断产品类型</dt><dd>研究员暂定判断</dd><small class="conflict-state">证据冲突，暂定判断</small></div>
      </dl>
    </header>
  `;
}

function renderVariantA() {
  return `
    <div class="variant-a" aria-label="判断优先工作台">
      <section class="judgment-band" aria-labelledby="judgment-title">
        <article class="judgment-primary">
          <h2 class="section-kicker" id="judgment-title">当前允许得出的判断</h2>
          <strong class="current-judgment">核心现金流仍有韧性，但当前价格要求 Cloud 利润扩张与 AI 投入回收同时成立。</strong>
          <p>现有证据只支持把判断维持在暂定状态。搜索广告提供底盘，Cloud 提供增量，AI 资本效率与监管结果决定上行是否真正转化为每股现金流。</p>
        </article>
        <article>
          <h3 class="section-kicker rust">最大反证</h3>
          <strong>资本开支持续快于收入增长，且搜索变现同时被新交互稀释。</strong>
          <small>两个因素若同时发生，现金流底盘与新增利润池都会弱于当前假设。</small>
        </article>
        <article>
          <h3 class="section-kicker amber">当前价格问题</h3>
          <strong>市场是否已经提前计入 Cloud 利润率达 24%，同时低估 AI 折旧压力？</strong>
          <small>价格本身不是结论，它决定需要验证的假设强度。</small>
        </article>
      </section>

      <section class="factor-section" aria-labelledby="factor-title">
        <div class="section-heading">
          <div><p class="section-kicker">判断骨架</p><h2 id="factor-title">四个关键因素</h2></div>
          <p>逐项维护机制、分歧和下一次可观察验证。</p>
        </div>
        <div class="factor-table-scroll">
          <table class="factor-table" data-factor-table>
            <colgroup><col class="factor-col-name" /><col class="factor-col-state" /><col class="factor-col-comparison" /><col class="factor-col-impact" /></colgroup>
            <thead>
              <tr class="factor-table-head">
                <th scope="col">关键因素 / 为什么关键</th><th scope="col">当前状态</th><th scope="col">House / Consensus / Implied</th><th scope="col">财务影响 / 下一验证</th>
              </tr>
            </thead>
            <tbody>${factors.map(renderFactorRow).join('')}</tbody>
          </table>
        </div>
      </section>

      <section class="business-transmission" aria-label="判断基础与传导">
        <div class="business-core">
          <p class="section-kicker">公司如何赚钱</p>
          <h2>用高意图入口产生现金，再把分发、数据与算力复用于 Cloud 和 AI。</h2>
          <p>搜索广告仍承担主要现金创造，Cloud 正在形成第二利润池。AI 既提升产品能力，也把更多现金提前转为基础设施。</p>
        </div>
        <div class="industry-position">
          <p class="section-kicker">行业位置</p>
          <h3>入口优势与算力规模并存，约束来自跨界竞争和监管补救。</h3>
          <p>优势不是单一市场份额，而是用户入口、广告需求、云平台和自研芯片之间的复用。</p>
        </div>
        <div class="valuation-transmission">
          <p class="section-kicker">预测与估值传导</p>
          <ol>
            <li><span>经营</span>搜索韧性 + Cloud 增量</li>
            <li><span>财务</span>利润率改善 − 折旧与资本开支</li>
            <li><span>价格</span>可持续每股自由现金流相对隐含假设</li>
          </ol>
        </div>
        <div class="largest-unknown">
          <p class="section-kicker rust">最大未知</p>
          <h3>AI 新增资本能否在搜索防守与 Cloud 增长之间形成可量化回报。</h3>
        </div>
        <div class="next-action" data-primary-next-action>
          <div><p class="section-kicker">唯一下一动作</p><h3>核对下一季资本开支、Cloud 利润率与搜索单位变现是否同向改善。</h3></div>
          <span class="next-action-label">建立 Q3 验证记录 <span aria-hidden="true">→</span></span>
        </div>
      </section>
    </div>
  `;
}

function renderVariantB() {
  return `
    <div class="variant-b" aria-label="模型优先工作台">
      <section class="driver-model" aria-labelledby="driver-title">
        <div class="driver-tree-panel">
          <div class="section-heading compact-heading"><div><p class="section-kicker">经营模型</p><h2 id="driver-title">经营驱动树</h2></div></div>
          <div class="driver-root">
            <span>每股自由现金流</span>
            <small>收入质量 × 增量利润 − 再投资</small>
          </div>
          <div class="driver-branches">
            ${factors.map((factor, index) => `
              <article class="driver-branch" data-factor-name="${factor.name}">
                <span>0${index + 1}</span>
                <div><h3>${factor.name}</h3><p>${factor.mechanism}</p><small>${factor.financialImpact}</small></div>
              </article>
            `).join('')}
          </div>
        </div>

        <div class="model-differences" aria-labelledby="difference-title">
          <p class="section-kicker">情景差异</p>
          <h2 id="difference-title">模型分歧</h2>
          <table class="model-ledger" data-model-table>
            <thead><tr class="model-ledger-head"><th scope="col">驱动</th><th scope="col">House</th><th scope="col">Consensus</th><th scope="col">Implied</th></tr></thead>
            <tbody>${factors.map((factor) => {
              const house = factor.metrics.find((metric) => metric.kind === 'House');
              const consensus = factor.metrics.find((metric) => metric.kind === 'Consensus');
              const implied = factor.metrics.find((metric) => metric.kind === 'Implied');
              return `<tr class="model-ledger-row"><th scope="row">${factor.name}</th><td>${metricValue(house)}</td><td>${metricValue(consensus)}</td><td>${metricValue(implied)}</td></tr>`;
            }).join('')}</tbody>
          </table>
          <div class="model-reading">
            <span>主要差异</span>
            <p>市场隐含更高的 Cloud 稳态利润率和更高资本强度，内部暂定假设对搜索韧性更积极，但对 AI 回收期更谨慎。</p>
          </div>
        </div>
      </section>

      <section class="model-judgment" aria-labelledby="model-judgment-title">
        <div><p class="section-kicker">模型之后</p><h2 id="model-judgment-title">判断摘要</h2></div>
        <p>在搜索收入增速高于一致预期、Cloud 利润率低于价格隐含值、资本强度介于两者之间的组合下，当前价格留下的容错并不充足。</p>
        <dl>
          <div><dt>最敏感驱动</dt><dd>Cloud 营业利润率</dd></div>
          <div><dt>最大未知</dt><dd>AI 新增资本的回收周期</dd></div>
          <div><dt>下一验证</dt><dd>2026 Q3 业绩口径</dd></div>
        </dl>
      </section>
    </div>
  `;
}

function renderVariantC() {
  return `
    <article class="pm-memo" aria-labelledby="memo-title">
      <header class="memo-heading">
        <p class="section-kicker">PM 备忘录 · 研究员暂定判断</p>
        <h2 id="memo-title">Alphabet：现金流韧性与 AI 再投资之间的价格约束</h2>
        <p>模拟数据 · 供内部讨论，按证据截止日维护。</p>
      </header>

      <section class="memo-section memo-question">
        <span class="memo-number">01</span>
        <div><h3>研究问题</h3><p>${RESEARCH_QUESTION}</p><small>3–5 年视角，具体证券为当前页面所示类别。</small></div>
      </section>

      <section class="memo-section memo-disagreements">
        <span class="memo-number">02</span>
        <div><h3>关键分歧</h3>
          <ol>${factors.map((factor) => `<li data-factor-name="${factor.name}"><strong>${factor.name}</strong><p>${factor.state}</p><small>${factor.unknown}</small></li>`).join('')}</ol>
        </div>
      </section>

      <section class="memo-section memo-valuation">
        <span class="memo-number">03</span>
        <div><h3>估值传导</h3><p>搜索收入韧性决定现金流底盘，Cloud 利润率决定增量利润，AI 资本强度决定其中多少能转为每股自由现金流。当前模拟价格隐含的 Cloud 利润率为 24.0%，高于 House 的 22.8% 与 Consensus 的 21.6%。</p></div>
      </section>

      <section class="memo-section memo-falsifier">
        <span class="memo-number">04</span>
        <div><h3>最强反证 / 证伪</h3><p>若资本开支强度持续上升，同时搜索单位变现走弱，现有判断失效。具体观察为：${factors[2].falsifier}</p></div>
      </section>

      <section class="memo-section memo-action" data-primary-next-action>
        <span class="memo-number">05</span>
        <div><h3>唯一下一动作</h3><p>用下一季披露同时核对资本开支、Cloud 营业利润率与搜索单位变现，三项口径齐备后再更新判断版本。</p><span class="next-action-label">建立 Q3 验证记录 <span aria-hidden="true">→</span></span></div>
      </section>
    </article>
  `;
}

function renderFactorRow(factor, index) {
  const house = factor.metrics.find((metric) => metric.kind === 'House');
  const consensus = factor.metrics.find((metric) => metric.kind === 'Consensus');
  const implied = factor.metrics.find((metric) => metric.kind === 'Implied');
  return `
    <tr class="factor-row" data-testid="factor-row" data-key-factor="${factor.id}">
      <th class="factor-name" scope="row">
        <button type="button" aria-expanded="false" aria-controls="factor-detail-${factor.id}">
          <span class="factor-index">0${index + 1}</span>
          <span><strong>${factor.name}</strong><small>${factor.whyKey}</small></span>
          <span class="factor-toggle" aria-hidden="true">＋</span>
        </button>
      </th>
      <td><p class="factor-state">${factor.state}</p></td>
      <td><div class="factor-comparison">
        ${metricChip(house)}${metricChip(consensus)}${metricChip(implied)}
      </div></td>
      <td><div class="factor-impact"><p>${factor.financialImpact}</p><small>${factor.nextValidation}</small></div></td>
    </tr>
    <tr class="factor-detail-row" hidden><td colspan="4">${renderFactorDetail(factor)}</td></tr>
  `;
}

function metricChip(metric) {
  return `<span class="metric-chip metric-${metric.kind.toLowerCase()}"><b>${metric.kind}</b>${metric.value}${metric.unit}</span>`;
}

function metricValue(metric) {
  return `<span class="model-${metric.kind.toLowerCase()}"><b>${metric.value}${metric.unit}</b><small>${metric.period}</small></span>`;
}

function renderFactorDetail(factor) {
  return `
    <section class="factor-detail" id="factor-detail-${factor.id}" data-testid="factor-detail" aria-label="${factor.name} 推理详情" hidden>
      <div class="reasoning-step reasoning-mechanism">
        <span>01</span><div><h3>机制假设</h3><p>${factor.mechanism}</p></div>
      </div>
      <div class="reasoning-step">
        <span>02</span><div><h3>观察事实</h3><ul>${factor.observations.map((item) => `<li>${item}</li>`).join('')}</ul></div>
      </div>
      <div class="reasoning-step">
        <span>03</span><div><h3>支持 / 反证 / 替代解释</h3>
          <dl class="reasoning-arguments">
            <div><dt>支持</dt><dd>${factor.support}</dd></div>
            <div><dt>反证</dt><dd>${factor.counter}</dd></div>
            <div><dt>替代解释</dt><dd>${factor.alternativeExplanation}</dd></div>
          </dl>
        </div>
      </div>
      <div class="reasoning-step">
        <span>04</span><div><h3>验证指标和日期</h3>${renderMetrics(factor.metrics)}<p class="validation-date">下一验证 · ${factor.nextValidation}</p></div>
      </div>
      <div class="reasoning-step">
        <span>05</span><div><h3>财务与估值影响</h3><p>${factor.financialImpact}</p><p><strong>证伪条件：</strong>${factor.falsifier}</p></div>
      </div>
      <div class="reasoning-step reasoning-conclusion">
        <span>06</span><div><h3>对当前判断的影响</h3><p>${factor.state}</p><p><strong>仍未知：</strong>${factor.unknown}</p></div>
      </div>
    </section>
  `;
}

function renderMetrics(metrics) {
  return `
    <div class="metric-table-scroll">
      <table class="metric-table" data-metric-table>
        <thead><tr class="metric-table-head"><th scope="col">类型</th><th scope="col">指标</th><th scope="col">数值</th><th scope="col">期间</th><th scope="col">单位</th><th scope="col">截至</th><th scope="col">来源边界</th></tr></thead>
        <tbody>${metrics.map((item) => `
          <tr class="metric-row metric-${item.kind.toLowerCase()}" data-as-of="${item.asOf}">
            <th scope="row">${item.kind}</th><td>${item.name}</td><td><b>${item.value}</b></td><td>${item.period}</td><td>${item.unit}</td><td>${item.asOf}</td><td>${item.sourceBoundary}</td>
          </tr>
        `).join('')}</tbody>
      </table>
    </div>
  `;
}

function metricSet(name, actual, guidance, consensus, implied, house, unit) {
  return [
    metric('Actual', name, actual, '2026 Q2', unit, '2026-07-23', '公司已披露'),
    metric('Guidance', name, guidance, '2026 Q3', unit, '2026-07-23', '管理层口径'),
    metric('Consensus', name, consensus, '2026 Q3', unit, '2026-08-18', '卖方一致预期'),
    metric('Implied', name, implied, '3–5 年', unit, '2026-08-19', '由模拟价格反推'),
    metric('House', name, house, '2026 Q3', unit, '2026-08-19', '内部暂定假设'),
  ];
}

function metric(kind, name, value, period, unit, asOf, sourceBoundary) {
  return { kind, name, value, period, unit, asOf, sourceBoundary };
}
