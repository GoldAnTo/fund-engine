/**
 * Page 6: 证据中心 (SPEC §9)
 * 内容: 来源清单、来源政策状态、原子陈述、支持/反驳关系、冲突、
 *       模型使用谱系、原文阅读入口。
 * 关键跳转: 打开精确原文阅读器或受影响的模型/结论。
 */
import { evidenceFor } from "../fixtures.js";

export function renderEvidence(state) {
  const items = evidenceFor(state.caseId);
  return `
    <div class="workbench">
      <header class="wb-context">
        <div>
          <span class="label">研究对象</span>
          <span class="value">${state.caseId === 'alphabet' ? 'Alphabet Inc. GOOGL' : '宁德时代 300750.SZ'}</span>
        </div>
        <div><span class="label">资料基础</span><span class="q-text">基础截止 2026-06-30 · ${items.length} 条原子陈述 (含反证与未知)</span></div>
        <div class="state-block">
          <span class="stage-tag"><span class="dot"></span> 证据中心</span>
          <a class="btn" href="?workface=reader&case=${state.caseId}" data-workface="reader">打开精确阅读器 →</a>
        </div>
      </header>

      <section>
        <header class="page-header">
          <div>
            <p class="eyebrow">06 / 07 · 证据中心</p>
            <h1>每一条陈述都有谱系</h1>
            <p class="muted">按 SPEC §6, 类型确定后只能用于一类输入; 反证与未知并列保留, 不被摘要省略。</p>
          </div>
        </header>
      </section>

      <div class="evidence-filters">
        <span class="filter-chip is-active">全部</span>
        <span class="filter-chip">事实 (fact)</span>
        <span class="filter-chip">指引 (guidance)</span>
        <span class="filter-chip">一致预期 (consensus)</span>
        <span class="filter-chip">假设 (assumption)</span>
        <span class="filter-chip">反证 (counter)</span>
        <span class="filter-chip">未知 (unknown)</span>
        <span class="muted" style="margin-left:auto;font-size:11px">特殊筛选: 阻塞项 · 反证 · 未复核</span>
      </div>

      <div class="page-overview">
        <div class="page-evidence" style="display:flex;flex-direction:column;gap:14px">
          ${items.map(it => evidenceRow(it, state.caseId)).join("")}

          ${state.caseId === "alphabet" ? conflictBlock() : ""}

          <article class="source-card">
            <div class="sc-head">
              <h4>来源政策 (SourceContract)</h4>
              <span class="tag tag--ok">采集 + 冻结 ok</span>
            </div>
            <dl class="sc-meta">
              <dt>来源机构/作者</dt><dd>公司披露 + 一致预期 + 外部调研</dd>
              <dt>采集策略</dt><dd>自动下载 (官方源) + 人工标注</dd>
              <dt>授权</dt><dd>${state.caseId === 'catl' ? '深交所披露 / 公开允许' : '10-K / Earnings call 公开'}</dd>
              <dt>留存</dt><dd>${state.caseId === 'catl' ? 'CSV/SQL 永久可读' : 'Sec 归档永久可读'}</dd>
              <dt>展示权</dt><dd>段落 + 被引单元格 (许可范围内)</dd>
              <dt>解析器</dt><dd>Docling v2.4 + PJ-Hash v1</dd>
            </dl>
            <p class="sc-lineage">每条材料冻结时记录内容哈希、抓取时间和解析器版本。任何后续材料的进入版本必须通过严格的来源准入, 否则只能作为未证实线索。</p>
          </article>
        </div>

        <aside class="shoulder">
          <article class="shoulder-card">
            <h3>谱系示例 · 一行</h3>
            <p style="font-size:12px;color:var(--c-ink-2)">
              <strong>现金循环周期 FY25 末 47 天</strong><br/>
              来源: 宁德 2025 年报 · P.43 · 营运能力分析表<br/>
              <code>0xffc0..2bd3</code> · 解析器 v2.4 · 抓取 2026-03-22<br/>
              流入: business_map → driver_map → memo §3<br/>
              反证: 无 · 复核: 已确认
            </p>
            <a href="?workface=reader&case=${state.caseId}" data-workface="reader">查看该陈述的原文 →</a>
          </article>

          <article class="shoulder-card">
            <h3>冲突并列保留</h3>
            <p class="muted" style="font-size:12px">当两个口径都合理时, 并列保留, 不由 AI 选择。冲突由用户决定采用哪个, 或接受两种并存。</p>
            <ul class="muted" style="font-size:11.5px">
              <li>· 管理层 Cloud 30% vs 一致 24% (Q1 2026)</li>
              <li>· 现行口径冲突在估值前必须先关</li>
            </ul>
          </article>

          <article class="shoulder-card">
            <h3>研究债务</h3>
            <p class="muted" style="font-size:12px">
              17 项开放缺口 (Alphabet 案例) / 4 项开放缺口 (CATL).
              每个缺口有一份"解决条件"。未解决前不可估值。
            </p>
          </article>
        </aside>
      </div>
    </div>
  `;
}

function evidenceRow(it, caseId) {
  return `
    <article class="evidence-row" data-type="${it.type}">
      <span class="ev-type-tag" data-type="${it.type}">${typeTag(it.type)}</span>
      <div class="ev-text">
        <strong>${it.text}</strong>
      </div>
      <div class="ev-meta">
        <span><strong>${it.srcRole}</strong></span>
        <span>${it.locator}</span>
        <span><code>${it.hash}</code></span>
      </div>
      <div class="ev-actions">
        <a class="btn" data-open="reader">查看原文</a>
        <small>${caseId === "catl" ? "映入财务桥接" : "— 阻塞估值"}</small>
      </div>
    </article>
  `;
}

function conflictBlock() {
  return `
    <article class="conflict-block">
      <h4>⚠ 来源冲突 · 并列保留 · 不由 AI 选择</h4>
      <div class="conflict-row">
        <div>
          <p class="conflict-head"><strong>管理层口径</strong> · Earnings call Q1 2026 transcript</p>
          <p class="value">+30% YoY</p>
          <p style="font-size:11px;color:var(--c-ink-muted)">Google Cloud 增速 (管理层自行归因 AI 推动)</p>
        </div>
        <div>
          <p class="conflict-head">一致预期 · Bloomberg 2026-06-29</p>
          <p class="value">+24% YoY</p>
          <p style="font-size:11px;color:var(--c-ink-muted)">n=27 卖方 · 抽样中位数</p>
        </div>
      </div>
      <p style="margin-top:8px;font-size:12px">两条并列保留。用户需在关键输入确认工作面中选择采用 / 并列 / 拒绝。冲突未解决前, Cloud 财务桥接不进行。</p>
    </article>
  `;
}

function typeTag(t) {
  return ({
    fact: "事实",
    guidance: "指引",
    consensus: "一致",
    assumption: "假设",
    counter: "反证",
    unknown: "未知",
  })[t] || t;
}
