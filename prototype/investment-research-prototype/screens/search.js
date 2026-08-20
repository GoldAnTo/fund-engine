const searchAliases = ['googl', 'alphabet', '谷歌', '云计算'];

export function renderSearch(root) {
  root.innerHTML = `
    <main class="search-screen" id="main-content">
      <section class="search-intro" aria-labelledby="search-title">
        <p class="eyebrow">新建公司研究</p>
        <h1 id="search-title">从一家公司开始</h1>
        <p class="search-lead">确认公司与证券身份，再提出真正值得回答的问题。</p>

        <div class="search-control">
          <svg aria-hidden="true" viewBox="0 0 24 24" width="21" height="21">
            <circle cx="10.8" cy="10.8" r="6.3"></circle>
            <path d="m15.5 15.5 4.2 4.2"></path>
          </svg>
          <input
            type="search"
            aria-label="搜索股票、公司或行业"
            placeholder="搜索股票、公司或行业"
            autocomplete="off"
            spellcheck="false"
          />
          <kbd aria-hidden="true">/</kbd>
        </div>

        <div class="search-stage" aria-live="polite"></div>
      </section>
    </main>
  `;

  const input = root.querySelector('input[type="search"]');
  const stage = root.querySelector('.search-stage');

  function updateResults() {
    const query = input.value.trim();
    const normalized = query.toLocaleLowerCase();
    const hasMatch = query && searchAliases.some((alias) => normalized.includes(alias) || alias.includes(normalized));
    stage.innerHTML = hasMatch ? resultGroups(query) : emptyState();
    bindExamples();
  }

  function bindExamples() {
    stage.querySelectorAll('[data-example]').forEach((button) => {
      button.addEventListener('click', () => {
        input.value = button.dataset.example;
        input.focus();
        updateResults();
      });
    });
  }

  input.addEventListener('input', updateResults);
  document.addEventListener('keydown', focusShortcut);
  updateResults();

  return () => document.removeEventListener('keydown', focusShortcut);

  function focusShortcut(event) {
    if (event.key === '/' && document.activeElement !== input) {
      event.preventDefault();
      input.focus();
    }
  }
}

function emptyState() {
  return `
    <div class="search-examples">
      <span>试试搜索</span>
      <button type="button" data-example="GOOGL">GOOGL</button>
      <button type="button" data-example="宁德时代">宁德时代</button>
      <button type="button" data-example="云计算基础设施">云计算基础设施</button>
    </div>
    <div class="search-note">
      <span class="note-index">01</span>
      <p>这里先辨认研究对象，不展示行情与资讯。公司、证券和行业会被分开呈现。</p>
    </div>
  `;
}

function resultGroups(query) {
  const matchMethod = query.toLocaleLowerCase().includes('goog') ? '代码匹配' : '名称匹配';
  return `
    <div class="result-summary">
      <span>匹配结果</span>
      <span>4 个实体</span>
    </div>
    <section class="result-group" aria-labelledby="security-heading">
      <div class="group-heading">
        <h2 id="security-heading">证券</h2>
        <span>2</span>
      </div>
      <div class="result-list">
        ${securityResult({ ticker: 'GOOGL', shareClass: 'Class A', matchMethod, dataGap: '细分资本开支口径待核对', href: '?screen=setup&security=GOOGL' })}
        ${securityResult({ ticker: 'GOOG', shareClass: 'Class C', matchMethod: '关联代码匹配', dataGap: '投票权差异需纳入判断', href: '?screen=setup&security=GOOG' })}
      </div>
    </section>
    <section class="result-group" aria-labelledby="company-heading">
      <div class="group-heading">
        <h2 id="company-heading">公司</h2>
        <span>1</span>
      </div>
      <a class="result-row compact-result" data-testid="company-result" href="?screen=setup&security=GOOGL">
        <span class="entity-monogram company-monogram" aria-hidden="true">A</span>
        <span class="result-identity">
          <strong>Alphabet Inc.</strong>
          <span>公司 · 名称匹配</span>
        </span>
        <span class="result-description">搜索、广告、云服务与前沿技术业务的母公司</span>
        <span class="result-arrow" aria-hidden="true">↗</span>
      </a>
    </section>
    <section class="result-group" aria-labelledby="industry-heading">
      <div class="group-heading">
        <h2 id="industry-heading">行业</h2>
        <span>1</span>
      </div>
      <div class="result-row compact-result" data-testid="industry-result">
        <span class="entity-monogram industry-monogram" aria-hidden="true">云</span>
        <span class="result-identity">
          <strong>云计算基础设施</strong>
          <span>行业 · 主题匹配</span>
        </span>
        <span class="result-description">数据中心、算力、云平台与基础模型服务</span>
      </div>
    </section>
  `;
}

function securityResult({ ticker, shareClass, matchMethod, dataGap, href }) {
  return `
    <a class="result-row security-result" data-testid="security-result" href="${href}" aria-label="${ticker} ${shareClass}，Alphabet Inc.">
      <span class="entity-monogram" aria-hidden="true">${ticker.slice(0, 1)}</span>
      <span class="result-identity">
        <strong>${ticker} <small>${shareClass}</small></strong>
        <span>证券 · ${matchMethod}</span>
      </span>
      <span class="security-facts">
        <span><b>交易所</b>NASDAQ</span>
        <span><b>货币</b>USD</span>
        <span><b>核心业务</b>搜索广告、云服务、AI</span>
        <span><b>覆盖截止</b>2026-06-30</span>
        <span class="data-gap"><b>数据缺口</b>${dataGap}</span>
      </span>
      <span class="result-arrow" aria-hidden="true">↗</span>
    </a>
  `;
}
