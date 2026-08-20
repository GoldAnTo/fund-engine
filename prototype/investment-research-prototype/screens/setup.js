const defaultQuestion = '以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？';
const questionTemplates = [
  ['竞争优势', '未来 3–5 年，哪些竞争优势能够持续，什么变化会削弱它们？'],
  ['资本回报', '未来资本投入如何转化为每股自由现金流，什么迹象会证明回报不及预期？'],
];

export function renderSetup(root, { navigate, params }) {
  const entity = entityFor(params.get('security'));
  const industry = params.get('industry') === 'cloud-infrastructure' ? '云计算基础设施' : '';
  const variant = entity.securityCode === 'GOOG' ? 'C' : 'A';
  const question = params.has('q') ? params.get('q') : entity.defaultQuestion;
  const horizon = ['1-2', '3-5', '5-plus'].includes(params.get('h')) ? params.get('h') : '3-5';
  const hypothesis = params.get('hp') ?? '';
  const concern = params.get('c') ?? '';

  root.innerHTML = `
    <main class="setup-screen" id="main-content">
      <a class="back-link" href="?screen=search" aria-label="返回公司搜索">
        <span aria-hidden="true">←</span> 返回搜索
      </a>

      <div class="setup-layout">
        <section class="setup-primary" aria-labelledby="setup-title">
          ${industry ? `<section class="industry-entry" aria-label="行业入口"><p class="eyebrow">行业入口 · ${industry}</p><strong>先从代表公司验证行业判断</strong><p>行业研究先落到可观察的代表公司，再把供需、竞争与利润池变化作为研究语境带入。</p></section>` : ''}
          <div class="identity-lockup">
            <span class="identity-mark" aria-hidden="true">${entity.mark}</span>
            <div>
              <p class="eyebrow">${industry ? '代表公司' : '已确认研究对象'}</p>
              <h1 id="setup-title">${entity.company}</h1>
              <p class="identity-security">${entity.securityLabel} <span>${entity.exchange}</span> <span>${entity.currency}</span></p>
            </div>
            <span class="verified-badge">
              <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m5.2 10.3 3 3 6.7-7"></path></svg>
              身份已确认
            </span>
          </div>

          <div class="company-context">
            <p><strong>核心业务</strong>${entity.business}</p>
            <p><strong>公司对应证券</strong>${entity.company} → ${entity.securityLabel}（${entity.exchange} · ${entity.currency}）</p>
            <p><strong>研究语境</strong>${industry ? `${industry}的供需、竞争与利润池变化将作为上下文，不直接生成宏观行业报告。` : entity.context}</p>
          </div>

          <form class="research-setup-form">
            <div class="question-field">
              <label for="research-question">你想回答什么问题？</label>
              <p id="question-help">问题会决定资料范围与后续判断结构，建立后仍可修订。</p>
              <div class="question-templates" aria-label="问题模板">
                ${questionTemplates.map(([label, value]) => `<button type="button" data-question-template="${escapeHtml(value)}">${label}模板</button>`).join('')}
              </div>
              <textarea id="research-question" name="question" rows="3" aria-describedby="question-help">${escapeHtml(question)}</textarea>
            </div>

            <fieldset class="horizon-fieldset">
              <legend>研究视角</legend>
              <div class="radio-row">
                ${radio('1–2 年', '1-2', horizon === '1-2')}
                ${radio('3–5 年', '3-5', horizon === '3-5')}
                ${radio('5 年以上', '5-plus', horizon === '5-plus')}
              </div>
            </fieldset>

            <details class="optional-ideas" ${hypothesis || concern ? 'open' : ''}>
              <summary>
                <span>
                  <strong>我已经有一些想法</strong>
                  <small>可选，帮助研究从你的判断起步</small>
                </span>
                <span class="summary-icon" aria-hidden="true">＋</span>
              </summary>
              <div class="ideas-fields">
                <label for="hypothesis">你的假设</label>
                <textarea id="hypothesis" name="hypothesis" rows="3" placeholder="例如：云业务的规模效应会抵消 AI 基础设施投入。">${escapeHtml(hypothesis)}</textarea>
                <label for="concern">你最担心什么？</label>
                <textarea id="concern" name="concern" rows="2" placeholder="例如：资本开支增长快于可持续现金回报。">${escapeHtml(concern)}</textarea>
              </div>
            </details>

            <div class="setup-actions">
              <p>下一步会进入研究工作台，你可以继续调整这些设置。</p>
              <button class="secondary-button" type="button" data-use-default>使用默认问题</button>
              <button class="primary-button" type="submit">
                建立研究
                <span aria-hidden="true">→</span>
              </button>
            </div>
          </form>
        </section>

        <aside class="research-preview" aria-labelledby="preview-title">
          <div class="preview-heading">
            <p class="eyebrow">研究预览</p>
            <span>系统建议，待确认</span>
          </div>
          <h2 id="preview-title">这项研究将从五个角度展开</h2>
          <ol class="preview-questions">
            ${entity.previewQuestions.map((item, index) => `<li><span>0${index + 1}</span><p>${item}</p></li>`).join('')}
          </ol>
          <div class="counter-question">
            <span>反向检验</span>
            <p>${entity.counterQuestion}</p>
          </div>
          <p class="preview-note">这些问题将作为初始框架，而不是预设答案。</p>
        </aside>
      </div>
    </main>
  `;

  const form = root.querySelector('form');
  root.querySelectorAll('[data-question-template]').forEach((button) => {
    button.addEventListener('click', () => {
      form.elements.question.value = button.dataset.questionTemplate;
      form.elements.question.focus();
    });
  });
  root.querySelector('[data-use-default]').addEventListener('click', () => submitFrame(entity.defaultQuestion));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submitFrame();
  });

  function submitFrame(forcedQuestion) {
    const formData = new FormData(form);
    const nextParams = {
      screen: 'workbench',
      security: entity.securityCode,
      variant,
      q: forcedQuestion ?? formData.get('question'),
      h: formData.get('horizon'),
    };
    if (industry) nextParams.industry = 'cloud-infrastructure';
    if (formData.get('hypothesis')) nextParams.hp = formData.get('hypothesis');
    if (formData.get('concern')) nextParams.c = formData.get('concern');
    navigate(nextParams);
  }
}

function entityFor(securityCode) {
  if (securityCode === '300750.SZ') return {
    securityCode: '300750.SZ', securityLabel: '300750.SZ', company: '宁德时代', mark: '宁',
    exchange: '深圳证券交易所', currency: 'CNY',
    business: '动力电池、储能电池与电池材料构成主要收入与资本投入方向。',
    context: '市场正在判断储能增长、海外产能利用率与电池价格下降如何共同影响资本回报。',
    defaultQuestion: '动力电池需求增长能否抵消单位价格下降，并转化为可持续自由现金流？',
    previewQuestions: [
      '动力电池需求增长能否抵消单位价格下降，并转化为可持续自由现金流？',
      '储能业务的增长与利润贡献如何验证？',
      '海外产能利用率何时能够覆盖新增折旧与资本成本？',
      '当前价格隐含了怎样的出货、单位利润与再投资假设？',
    ],
    counterQuestion: '什么事实会证明规模增长无法覆盖价格下降与新增资本投入？',
  };
  if (securityCode === 'GOOG') return {
    securityCode: 'GOOG', securityLabel: 'GOOG Class C', company: 'Alphabet', mark: 'A',
    exchange: 'NASDAQ', currency: 'USD',
    business: '搜索与广告构成现金流基础，Google Cloud 与 AI 基础设施扩展长期增长边界。',
    context: '市场正在重新判断资本开支、AI 分发优势与云业务利润率之间的关系。',
    defaultQuestion,
    previewQuestions: [
      '核心业务如何获得用户注意力，并把它转化为可持续收入？',
      '搜索分发、数据与计算基础设施形成了怎样的竞争优势？',
      '云服务与 AI 投入会如何改变利润率和资本回报？',
      '当前价格隐含了怎样的增长、盈利与再投资假设？',
    ],
    counterQuestion: '什么事实会证明核心业务增长无法转化为每股自由现金流？',
  };
  return {
    securityCode: 'GOOGL', securityLabel: 'GOOGL Class A', company: 'Alphabet', mark: 'A',
    exchange: 'NASDAQ', currency: 'USD',
    business: '搜索与广告构成现金流基础，Google Cloud 与 AI 基础设施扩展长期增长边界。',
    context: '市场正在重新判断资本开支、AI 分发优势与云业务利润率之间的关系。',
    defaultQuestion,
    previewQuestions: [
      '核心业务如何获得用户注意力，并把它转化为可持续收入？',
      '搜索分发、数据与计算基础设施形成了怎样的竞争优势？',
      '云服务与 AI 投入会如何改变利润率和资本回报？',
      '当前价格隐含了怎样的增长、盈利与再投资假设？',
    ],
    counterQuestion: '什么事实会证明核心业务增长无法转化为每股自由现金流？',
  };
}

function radio(label, value, checked = false) {
  return `
    <label class="radio-option">
      <input type="radio" name="horizon" value="${value}" ${checked ? 'checked' : ''} />
      <span>${label}</span>
    </label>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}
