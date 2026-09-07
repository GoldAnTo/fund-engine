/**
 * Page 1: 研究库 (SPEC §9)
 * 内容: 搜索公司/证券、开始研究、继续最近研究、研究状态和待处理项
 *       没有新闻流或选股排行
 */
import {
  catlCompany, alphaCompany, catlResearch, alphaResearch,
} from "../fixtures.js";

export function renderLibrary(state) {
  if (state.caseId === "alphabet") {
    return renderLibraryFor("alphabet", alphaCompany, alphaResearch);
  }
  return renderLibraryFor("catl", catlCompany, catlResearch);
}

function renderLibraryFor(caseId, company, research) {
  return `
    <div class="workbench">
      <header class="page-header">
        <div>
          <p class="eyebrow">01 / 07 · 研究库</p>
          <h1>选择公司开始研究</h1>
          <p class="muted">先确认公司与证券身份。可选输入关注问题;截止日、情景与估值策略由系统采用版本化默认值。</p>
        </div>
        <div class="page-actions">
          <button class="btn">最近研究</button>
          <button class="btn btn--primary">新建研究</button>
        </div>
      </header>

      <section class="search-bar">
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="10.5" cy="10.5" r="6.5"></circle><path d="m15.5 15.5 4 4"></path></svg>
        <input type="search" placeholder="搜索公司名称或证券代码（GOOGL / 300750.SZ / 宁德时代 / Alphabet）" autocomplete="off" />
        <span class="hint">↩ 打开 · ⌥↩ 新建证券</span>
      </section>

      <section class="search-results">
        ${resultCard(caseId, company)}
        ${researchCard(caseId, company, research)}
        ${recentSearchCard()}
      </section>

      <section>
        <header class="section-head">
          <h2>最近研究</h2>
          <span class="sub">按截止日倒序 · 任何点击都会切换到对应 case</span>
        </header>
        <ul class="recent-runs">
          ${recentRow(caseId, research)}
          ${otherRow("catl", catlCompany, catlResearch)}
          ${otherRow("alphabet", alphaCompany, alphaResearch)}
          ${otherRow("alphabet", alphaCompany, alphaResearch, "ARCHIVED · 2025/12 测试冻结", "completed")}
        </ul>
      </section>

      <section>
        <header class="section-head">
          <h2>设计说明</h2>
        </header>
        <p class="muted" style="font-size:12px;max-width:78ch">
          研究库没有新闻流、热度榜单、热门主题或全市场选股器。
          切换到 Alphabet 不可回答案例可体验"阻塞"状态：
          进入即提示 17 项缺口, 不进入研究概览或估值, 而要求先关闭阻塞项。
          双案例对照是 SPEC §12.4 的验收要求。
        </p>
      </section>
    </div>
  `;
}

function resultCard(caseId, company) {
  return `
    <article class="search-card">
      <header class="row">
        <strong>证券</strong>
        <span class="tag tag--ok">1 个匹配</span>
      </header>
      <h3>${company.ticker}${company.trading_currency === "CNY" ? " <small>CATL</small>" : " <small>Class A</small>"}</h3>
      <p class="muted" style="font-size:12px">${company.exchange} · ${company.trading_currency}</p>
      <p class="muted" style="font-size:12px">对应公司 → ${company.canonical_name}</p>
      <p class="muted" style="font-size:12px">核心业务 → ${company.industry}</p>
      <p class="muted" style="font-size:12px">样本数据截止 → 2026-06-30</p>
      <div class="tags">
        <span class="tag tag--gap">细分资本开支口径待核对</span>
      </div>
      <a class="link-to" href="${caseId === "catl" ? "/overview?case=catl" : "/overview?case=alphabet"}">打开研究 →</a>
    </article>
  `;
}

function researchCard(caseId, company, research) {
  const blockerCount = (research.blockers || []).length;
  return `
    <article class="search-card">
      <header class="row">
        <strong>研究范围</strong>
        <span class="tag tag--${research.answerability === "answerable" ? "ok" : "block"}">${research.answerability === "answerable" ? "可保存" : "阻塞"}</span>
      </header>
      <h3>${company.canonical_name}</h3>
      <p class="muted" style="font-size:12px">${research.question}</p>
      <dl style="margin-top:8px;font-size:12px">
        <dt class="row"><span>截止</span><strong>${research.cutoff_at}</strong></dt>
        <dt class="row"><span>资料数 / 已复核</span><strong>${research.context_meta.sources_active} / ${research.context_meta.sources_reviewed}</strong></dt>
        <dt class="row"><span>已确认事实</span><strong>${research.context_meta.evidence_facts_confirmed}</strong></dt>
        <dt class="row"><span>开放缺口</span><strong style="color:${research.context_meta.gaps_open > 4 ? 'var(--c-failed)' : 'inherit'}">${research.context_meta.gaps_open}</strong></dt>
      </dl>
      <div class="tags">
        ${research.answerability === "answerable"
          ? `<span class="tag tag--ok">基础 ID 已冻结</span>`
          : `<span class="tag tag--block">${research.context_meta.gaps_open} 项阻塞</span>`}
        <span class="tag tag--pending">${research.strategy_version}</span>
      </div>
      <a class="link-to" href="${research.answerability === "answerable" ? "/overview?case=" + caseId : "/overview?case=" + caseId}">继续研究 →</a>
    </article>
  `;
}

function recentSearchCard() {
  return `
    <article class="search-card">
      <header class="row">
        <strong>历史样本</strong>
        <span class="tag">3 个</span>
      </header>
      <h3>2024 决策工作流原型</h3>
      <p class="muted" style="font-size:12px">历史事件研究 prototype · production 端不再暴露</p>
      <ul style="font-size:12px;margin:6px 0">
        <li class="row"><span>事件研究 a</span><span class="muted">已弃用</span></li>
        <li class="row"><span>主题跨切</span><span class="muted">已弃用</span></li>
        <li class="row"><span>资料库早期版</span><span class="muted">迁移完成</span></li>
      </ul>
      <p class="muted" style="font-size:11px">为避免暴露旧工作流, 不从研究库直接打开历史。</p>
    </article>
  `;
}

function recentRow(caseId, research) {
  // 当前 case 高亮
  return `
    <li class="recent-row ${caseId === "catl" ? "is-completed" : "is-blocked"}">
      <div>
        <p class="name">${caseId === "catl" ? "宁德时代海外扩张" : "Alphabet AI 资本开支"}</p>
        <p class="muted" style="font-size:11px">run-id ${research.run_id}</p>
      </div>
      <div>
        <p class="muted" style="font-size:11px">${research.context_meta.basis_window}</p>
        <p style="font-size:11px">${research.evidence_facts_count || research.context_meta.sources_active} 项已评审</p>
      </div>
      <div class="progress" aria-label="进度"><span class="fill" style="width:${caseId === 'catl' ? '95' : '22'}%"></span></div>
      <span class="tag tag--${caseId === "catl" ? "ok" : "block"}">${caseId === "catl" ? "待确认 6 项" : "阻塞 17 项"}</span>
      <a class="btn" href="/overview?case=${caseId}">打开</a>
    </li>
  `;
}

function otherRow(caseId, company, research, label, statusClass = "is-blocked") {
  return `
    <li class="recent-row ${statusClass}">
      <div>
        <p class="name">${company.canonical_name} · 季度更新</p>
        <p class="muted" style="font-size:11px">${label || `基于 basis ${research.historical_basis_id}`}</p>
      </div>
      <div>
        <p class="muted" style="font-size:11px">2026-Q1 评估</p>
        <p style="font-size:11px">${research.context_meta.sources_active} 项资料 · ${research.context_meta.gaps_closed} 项缺口已关闭</p>
      </div>
      <div class="progress" aria-label="进度"><span class="fill" style="width:${statusClass === 'is-completed' ? 100 : 50}%"></span></div>
      <span class="tag tag--${statusClass === 'is-completed' ? 'ok' : 'pending'}">${statusClass === 'is-completed' ? '已冻结 v2' : 'Draft 仅保存'}</span>
      <a class="btn" href="/overview?case=${caseId}">回放</a>
    </li>
  `;
}
