(function () {
  "use strict";

  const data = window.PROTOTYPE_DATA;
  const app = document.querySelector("#app");

  const NAV_ITEMS = [
    { label: "工作台", screen: "overview", icon: "台" },
    { label: "研究案例", screen: "case", icon: "研" },
    { label: "资料与知识", screen: "library", icon: "知" },
    { label: "数据中心", screen: "data", icon: "数" },
    { label: "审核中心", screen: "review", icon: "审" },
    { label: "监测与更新", screen: "versions", icon: "更" },
  ];

  const PLACEHOLDERS = {
    "new-research": ["创建研究", "定义一个可证伪问题，并明确截止日与首次冻结边界。"],
    plan: ["研究计划", "组织候选因素、反证方向与下一验证事件；正式化仍需人工操作。"],
    case: ["研究案例", "后续任务将在此呈现同一 ResearchCase 的命题、证据与时间版本。"],
    graph: ["证据图谱", "证据台账是事实源；这里仅承载可重建的关联投影。"],
    review: ["审核中心", "待审核事实不会自动进入正式命题、关系或冻结快照。"],
    library: ["资料与知识", "按来源版本、原文区段和可用时间管理研究材料。"],
    data: ["数据中心", "展示数据提供方运行状态、缺口与人工补录边界。"],
    versions: ["版本与更新", "沿用一个持续研究案例，通过截止日和冻结快照回看历史。"],
  };

  function escapeHTML(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function badge(label, state, iconLabel) {
    return `<span class="state-badge ${escapeHTML(state)}" aria-label="${escapeHTML(iconLabel ?? label)}">${escapeHTML(label)}</span>`;
  }

  function screenHeader(eyebrow, title, lede) {
    return `
      <p class="eyebrow">${escapeHTML(eyebrow)}</p>
      <h1>${escapeHTML(title)}</h1>
      <p class="lede">${escapeHTML(lede)}</p>
    `;
  }

  function renderOverview() {
    const activeCase = data.case;
    const pendingReview = data.reviewQueue[0];
    const pendingEvidence = data.evidenceLinks.find((item) => item.reviewState === "pending_review");
    const blockerThesis = data.theses.find((item) => item.reviewState === "pending_review");
    const contradiction = data.factors.find((item) => item.group === "contradiction");
    const revisedMetric = data.metrics.find((item) => item.id === "M-NVDA-DC-REV");
    const providerFailures = data.providerRuns.filter((item) => ["quota_failure", "permission_gap"].includes(item.outcome));
    const recentSnapshot = data.snapshots.find((item) => item.id === activeCase.snapshotId);
    const hasPriorMetricVersion = revisedMetric.snapshotMembership.some((snapshotId) => snapshotId !== activeCase.snapshotId);

    return `
      <section class="screen" data-screen="overview">
        <div class="overview-heading">
          <div>
            <p class="eyebrow">Research workspace</p>
            <h1>研究总览</h1>
            <p class="lede">先处理阻塞判断的工作，再沿冻结版本继续同一个 ResearchCase。</p>
          </div>
          <a class="primary-action" data-primary-action href="?screen=new-research">新建研究</a>
        </div>

        <section class="queue-section" aria-labelledby="research-queue-title">
          <div class="section-heading">
            <div>
              <p class="section-kicker">当前工作焦点</p>
              <h2 id="research-queue-title">ResearchCase 队列</h2>
            </div>
            <p>按下一步行动排序 · 仅显示一个持续研究案例</p>
          </div>

          <article class="case-row selected" data-research-case-row aria-selected="true">
            <div class="case-main">
              <div class="case-identity">
                <span class="case-id">${escapeHTML(activeCase.id)}</span>
                ${badge("进行中 · 待验证", "warning", "研究案例状态：进行中，待验证")}
              </div>
              <h3>${escapeHTML(activeCase.title)}</h3>
              <p class="case-question">${escapeHTML(activeCase.question)}</p>
              <dl class="case-facts">
                <div><dt>截止日</dt><dd>${escapeHTML(activeCase.cutoff)}</dd></div>
                <div><dt>当前快照</dt><dd>${escapeHTML(activeCase.snapshotId)}</dd></div>
                <div><dt>人工复核状态</dt><dd data-state-label>待人工审核 · ${escapeHTML(activeCase.state)}</dd></div>
              </dl>
              <div class="assessment compact-assessment" data-evidence-assessment>
                <strong>${escapeHTML(activeCase.aiLabel)}</strong>
                <p>${escapeHTML(activeCase.provisionalAssessment)}</p>
              </div>
            </div>
            <div class="case-decision">
              <p class="decision-label">主要阻塞</p>
              <h4>${escapeHTML(blockerThesis.title)}</h4>
              <p>${escapeHTML(pendingReview.task)}</p>
              <div class="decision-source">
                <span>${escapeHTML(pendingEvidence.id)}</span>
                <span>${escapeHTML(pendingEvidence.sourceVersion)}</span>
              </div>
              <a class="next-action" data-next-action href="?screen=review">审核订单到收入关系 <span aria-hidden="true">→</span></a>
            </div>
          </article>
        </section>

        <section class="status-section" aria-labelledby="status-lanes-title">
          <div class="section-heading compact-heading">
            <div>
              <p class="section-kicker">需要注意</p>
              <h2 id="status-lanes-title">研究状态线</h2>
            </div>
            <p>所有状态均保留来源标识，不以颜色代替含义</p>
          </div>
          <div class="status-lanes">
            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(pendingReview.id)}">
              <div class="lane-heading"><span class="lane-mark warning" aria-hidden="true">审</span><h3>待审核关系</h3></div>
              <p class="lane-state" data-state-label>待人工审核</p>
              <strong>${escapeHTML(pendingReview.task)}</strong>
              <p class="lane-detail">${escapeHTML(pendingReview.targetId)} · ${escapeHTML(pendingReview.sourceVersion)}</p>
            </article>

            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(contradiction.id)}">
              <div class="lane-heading"><span class="lane-mark contradict" aria-hidden="true">反</span><h3>新反面证据</h3></div>
              <p class="lane-state" data-state-label>候选反证 · 待关联来源</p>
              <strong>${escapeHTML(contradiction.label)}</strong>
              <p class="lane-detail">${escapeHTML(contradiction.id)} · ${escapeHTML(contradiction.status)}</p>
            </article>

            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(revisedMetric.id)}">
              <div class="lane-heading"><span class="lane-mark gap" aria-hidden="true">缺</span><h3>数据修订与缺口</h3></div>
              <p class="lane-state" data-state-label>${hasPriorMetricVersion ? "已有跨版本口径" : "缺少前次快照对照"}</p>
              <strong>${escapeHTML(revisedMetric.name)} · ${escapeHTML(revisedMetric.value)}</strong>
              <p class="lane-detail">${escapeHTML(revisedMetric.period)} · ${escapeHTML(revisedMetric.sourceVersion)}</p>
            </article>

            <article class="status-lane provider-lane" data-support-lane data-source-id="${escapeHTML(providerFailures.map((item) => item.id).join(","))}">
              <div class="lane-heading"><span class="lane-mark provider" aria-hidden="true">源</span><h3>Provider 状态</h3></div>
              ${providerFailures.map((run) => `
                <div class="provider-item">
                  <p class="lane-state" data-state-label>${escapeHTML(run.outcome)}</p>
                  <strong>${escapeHTML(run.provider)}</strong>
                  <p class="lane-detail">${escapeHTML(run.detail)}</p>
                </div>
              `).join("")}
            </article>

            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(recentSnapshot.id)}">
              <div class="lane-heading"><span class="lane-mark frozen" aria-hidden="true">冻</span><h3>最近冻结版本</h3></div>
              <p class="lane-state" data-state-label>${escapeHTML(recentSnapshot.label)} · 已冻结</p>
              <strong>${escapeHTML(recentSnapshot.id)}</strong>
              <p class="lane-detail">截止 ${escapeHTML(recentSnapshot.cutoff)}<br>冻结于 ${escapeHTML(recentSnapshot.frozenAt)}</p>
            </article>
          </div>
        </section>
      </section>
    `;
  }

  function renderPlaceholder(screen) {
    const [title, description] = PLACEHOLDERS[screen];
    return `
      <section class="screen" data-screen="${escapeHTML(screen)}">
        ${screenHeader("Prototype route", title, description)}
        <div class="paper-card placeholder">
          <div class="placeholder-inner">
            <span class="placeholder-icon" aria-hidden="true">◇</span>
            <h2>${escapeHTML(title)}界面将在后续任务实现</h2>
            <p>当前仅保留明确路由和产品边界，不把占位内容描述为已交付能力。</p>
          </div>
        </div>
      </section>
    `;
  }

  const SCREEN_RENDERERS = {
    "overview": renderOverview,
    "new-research": () => renderPlaceholder("new-research"),
    "plan": () => renderPlaceholder("plan"),
    "case": () => renderPlaceholder("case"),
    "graph": () => renderPlaceholder("graph"),
    "review": () => renderPlaceholder("review"),
    "library": () => renderPlaceholder("library"),
    "data": () => renderPlaceholder("data"),
    "versions": () => renderPlaceholder("versions"),
  };

  function requestedScreen() {
    const candidate = new URLSearchParams(window.location.search).get("screen") ?? "overview";
    return Object.hasOwn(SCREEN_RENDERERS, candidate) ? candidate : "overview";
  }

  function renderShell(screen) {
    const activeNav = NAV_ITEMS.find((item) => item.screen === screen)?.screen ?? "overview";
    app.innerHTML = `
      <div class="app-shell">
        <aside class="nav-rail" aria-label="主导航">
          <div class="brand">
            <span class="brand-mark" aria-hidden="true">R</span>
            <div><strong>研究台账</strong><small>EVIDENCE LEDGER</small></div>
          </div>
          <nav class="nav-list">
            ${NAV_ITEMS.map((item) => `
              <a class="nav-link" href="?screen=${escapeHTML(item.screen)}"${item.screen === activeNav ? ' aria-current="page"' : ""}>
                <span class="nav-icon" aria-hidden="true">${escapeHTML(item.icon)}</span>
                <span>${escapeHTML(item.label)}</span>
              </a>
            `).join("")}
          </nav>
          <p class="nav-note">证据台账为事实源<br>图谱与搜索均为可重建投影</p>
        </aside>
        <header class="utility-header">
          <div class="breadcrumbs"><span>AI 算力产业链</span><span aria-hidden="true">/</span><strong>${escapeHTML(data.case.snapshotId)}</strong></div>
          <div class="utility-actions"><span class="utility-pill">截止 ${escapeHTML(data.case.cutoff)}</span><span class="utility-pill">只读原型</span></div>
        </header>
        <div class="work-area">${SCREEN_RENDERERS[screen]()}</div>
      </div>
    `;
  }

  renderShell(requestedScreen());
  window.SCREEN_RENDERERS = SCREEN_RENDERERS;
}());
