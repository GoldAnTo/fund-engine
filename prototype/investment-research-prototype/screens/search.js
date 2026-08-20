const alphabetAliases = ['googl', 'alphabet', '谷歌', '云计算'];
const catlAliases = ['catl', '300750', '宁德时代'];

export function renderSearch(root, { navigate }) {
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

        <p class="search-result-status" role="status" aria-live="polite" aria-atomic="true"></p>
        <div class="search-stage"></div>
      </section>
    </main>
  `;

  const input = root.querySelector('input[type="search"]');
  const stage = root.querySelector('.search-stage');
  const status = root.querySelector('.search-result-status');

  function updateResults() {
    const query = input.value.trim();
    const normalized = query.toLocaleLowerCase();
    const matchesCatl = query && catlAliases.some((alias) => normalized.includes(alias) || alias.includes(normalized));
    const matchesAlphabet = query && alphabetAliases.some((alias) => normalized.includes(alias) || alias.includes(normalized));
    stage.innerHTML = matchesCatl ? catlResultGroups(query) : matchesAlphabet ? alphabetResultGroups(query) : emptyState();
    status.textContent = matchesCatl
      ? '找到 2 个原型样本实体'
      : matchesAlphabet
        ? '找到 4 个原型样本实体'
        : query
          ? '未找到原型样本实体'
          : '等待输入搜索词';
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
  root.addEventListener('click', navigateResult);
  document.addEventListener('keydown', focusShortcut);
  updateResults();

  return () => {
    root.removeEventListener('click', navigateResult);
    document.removeEventListener('keydown', focusShortcut);
  };

  function navigateResult(event) {
    const link = event.target.closest('a[href*="screen=setup"]');
    if (!link || !root.contains(link)) return;
    event.preventDefault();
    navigate(Object.fromEntries(new URL(link.href).searchParams));
  }

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

function alphabetResultGroups(query) {
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
        ${securityResult({ ticker: 'GOOGL', shareClass: 'Class A', matchMethod, company: 'Alphabet Inc.', dataGap: '细分资本开支口径待核对', href: '?screen=setup&security=GOOGL' })}
        ${securityResult({ ticker: 'GOOG', shareClass: 'Class C', matchMethod: '关联代码匹配', company: 'Alphabet Inc.', dataGap: '投票权差异需纳入判断', href: '?screen=setup&security=GOOG' })}
      </div>
    </section>
    <section class="result-group" aria-labelledby="company-heading">
      <div class="group-heading">
        <h2 id="company-heading">公司</h2>
        <span>1</span>
      </div>
      <details class="company-selector" data-testid="company-result">
        <summary class="result-row compact-result">
          <span class="entity-monogram company-monogram" aria-hidden="true">A</span>
          <span class="result-identity">
            <strong>Alphabet Inc.</strong>
            <span>公司 · 名称匹配</span>
          </span>
          <span class="result-description">搜索、广告、云服务与前沿技术业务的母公司</span>
          <span class="result-arrow" aria-hidden="true">＋</span>
        </summary>
        <div class="company-security-choices">
          <div><strong>请选择具体证券</strong><p>公司主体对应两个上市股权类别，研究必须绑定具体证券与投票权类别。</p></div>
          <a href="?screen=setup&security=GOOGL" aria-label="选择 GOOGL Class A"><b>GOOGL Class A</b><span>NASDAQ · 有投票权</span></a>
          <a href="?screen=setup&security=GOOG" aria-label="选择 GOOG Class C"><b>GOOG Class C</b><span>NASDAQ · 无投票权</span></a>
        </div>
      </details>
    </section>
    <section class="result-group" aria-labelledby="industry-heading">
      <div class="group-heading">
        <h2 id="industry-heading">行业</h2>
        <span>1</span>
      </div>
      <a class="result-row compact-result" data-testid="industry-result" href="?screen=setup&security=GOOGL&industry=cloud-infrastructure" aria-label="云计算基础设施，从代表公司 Alphabet 开始">
        <span class="entity-monogram industry-monogram" aria-hidden="true">云</span>
        <span class="result-identity">
          <strong>云计算基础设施</strong>
          <span>行业 · 主题匹配</span>
        </span>
        <span class="result-description">数据中心、算力、云平台与基础模型服务</span>
        <span class="result-arrow" aria-hidden="true">↗</span>
      </a>
    </section>
  `;
}

function catlResultGroups(query) {
  const matchMethod = query.toLocaleLowerCase().includes('300750') ? '代码匹配' : '名称匹配';
  return `
    <div class="result-summary" data-catl-results>
      <span>匹配结果</span>
      <span>2 个实体 · 研究覆盖有限</span>
    </div>
    <section class="result-group" aria-labelledby="catl-security-heading" data-catl-results>
      <div class="group-heading">
        <h2 id="catl-security-heading">证券</h2>
        <span>1</span>
      </div>
      <div class="result-list">
        <a class="result-row security-result" data-testid="catl-security-result" href="?screen=setup&security=300750.SZ" aria-label="300750.SZ CATL，宁德时代新能源科技股份有限公司">
          <span class="entity-monogram" aria-hidden="true">C</span>
          <span class="result-identity">
            <strong>300750.SZ <small>CATL</small></strong>
            <span>证券 · ${matchMethod}</span>
          </span>
          <span class="security-facts">
            <span><b>交易所</b>深圳证券交易所</span>
            <span><b>货币</b>CNY</span>
            <span><b>公司对应证券</b>宁德时代 → 300750.SZ</span>
            <span><b>核心业务</b>动力电池、储能电池与电池材料</span>
            <span><b>原型样本数据截止</b>2026-06-30</span>
            <span class="data-gap"><b>数据缺口</b>细分出货量与海外产能口径待补齐</span>
          </span>
          <span class="result-arrow" aria-hidden="true">↗</span>
        </a>
      </div>
    </section>
    <section class="result-group" aria-labelledby="catl-company-heading" data-catl-results>
      <div class="group-heading">
        <h2 id="catl-company-heading">公司</h2>
        <span>1</span>
      </div>
      <a class="result-row compact-result" data-testid="catl-company-result" href="?screen=setup&security=300750.SZ">
        <span class="entity-monogram company-monogram" aria-hidden="true">宁</span>
        <span class="result-identity">
          <strong>宁德时代新能源科技股份有限公司</strong>
          <span>公司 · 关联实体</span>
        </span>
        <span class="result-description">CATL · 公司对应证券：300750.SZ</span>
        <span class="result-arrow" aria-hidden="true">↗</span>
      </a>
    </section>
  `;
}

function securityResult({ ticker, shareClass, matchMethod, company, dataGap, href }) {
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
        <span><b>公司对应证券</b>${company} → ${ticker}</span>
        <span><b>核心业务</b>搜索广告、云服务、AI</span>
        <span><b>原型样本数据截止</b>2026-06-30</span>
        <span class="data-gap"><b>数据缺口</b>${dataGap}</span>
      </span>
      <span class="result-arrow" aria-hidden="true">↗</span>
    </a>
  `;
}
