const defaultQuestion = '以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？';

export function renderSetup(root, { navigate, params }) {
  const security = params.get('security') === 'GOOG' ? 'GOOG Class C' : 'GOOGL Class A';
  const variant = security.includes('Class C') ? 'C' : 'A';

  root.innerHTML = `
    <main class="setup-screen" id="main-content">
      <a class="back-link" href="?screen=search" aria-label="返回公司搜索">
        <span aria-hidden="true">←</span> 返回搜索
      </a>

      <div class="setup-layout">
        <section class="setup-primary" aria-labelledby="setup-title">
          <div class="identity-lockup">
            <span class="identity-mark" aria-hidden="true">A</span>
            <div>
              <p class="eyebrow">已确认研究对象</p>
              <h1 id="setup-title">Alphabet</h1>
              <p class="identity-security">${security} <span>NASDAQ</span> <span>USD</span></p>
            </div>
            <span class="verified-badge">
              <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m5.2 10.3 3 3 6.7-7"></path></svg>
              身份已确认
            </span>
          </div>

          <div class="company-context">
            <p><strong>核心业务</strong>搜索与广告构成现金流基础，Google Cloud 与 AI 基础设施扩展长期增长边界。</p>
            <p><strong>研究语境</strong>市场正在重新判断资本开支、AI 分发优势与云业务利润率之间的关系。</p>
          </div>

          <form class="research-setup-form">
            <div class="question-field">
              <label for="research-question">你想回答什么问题？</label>
              <p id="question-help">问题会决定资料范围与后续判断结构，建立后仍可修订。</p>
              <textarea id="research-question" name="question" rows="3" aria-describedby="question-help">${defaultQuestion}</textarea>
            </div>

            <fieldset class="horizon-fieldset">
              <legend>研究视角</legend>
              <div class="radio-row">
                ${radio('1–2 年', '1-2')}
                ${radio('3–5 年', '3-5', true)}
                ${radio('5 年以上', '5-plus')}
              </div>
            </fieldset>

            <details class="optional-ideas">
              <summary>
                <span>
                  <strong>我已经有一些想法</strong>
                  <small>可选，帮助研究从你的判断起步</small>
                </span>
                <span class="summary-icon" aria-hidden="true">＋</span>
              </summary>
              <div class="ideas-fields">
                <label for="hypothesis">你的假设</label>
                <textarea id="hypothesis" rows="3" placeholder="例如：云业务的规模效应会抵消 AI 基础设施投入。"></textarea>
                <label for="concern">你最担心什么？</label>
                <textarea id="concern" rows="2" placeholder="例如：资本开支增长快于可持续现金回报。"></textarea>
              </div>
            </details>

            <div class="setup-actions">
              <p>下一步会进入研究工作台，你可以继续调整这些设置。</p>
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
            <li><span>01</span><p>核心业务如何获得用户注意力，并把它转化为可持续收入？</p></li>
            <li><span>02</span><p>搜索分发、数据与计算基础设施形成了怎样的竞争优势？</p></li>
            <li><span>03</span><p>云服务与 AI 投入会如何改变利润率和资本回报？</p></li>
            <li><span>04</span><p>当前价格隐含了怎样的增长、盈利与再投资假设？</p></li>
          </ol>
          <div class="counter-question">
            <span>反向检验</span>
            <p>什么事实会证明核心业务增长无法转化为每股自由现金流？</p>
          </div>
          <p class="preview-note">这些问题将作为初始框架，而不是预设答案。</p>
        </aside>
      </div>
    </main>
  `;

  root.querySelector('form').addEventListener('submit', (event) => {
    event.preventDefault();
    navigate({ screen: 'workbench', security: variant === 'A' ? 'GOOGL' : 'GOOG', variant });
  });
}

function radio(label, value, checked = false) {
  return `
    <label class="radio-option">
      <input type="radio" name="horizon" value="${value}" ${checked ? 'checked' : ''} />
      <span>${label}</span>
    </label>
  `;
}
