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
    const reviewed = data.evidenceLinks.filter((item) => item.reviewState === "reviewed").length;
    const pending = data.evidenceLinks.filter((item) => item.reviewState === "pending_review").length;

    return `
      <section class="screen" data-screen="overview">
        ${screenHeader("ResearchCase workspace", "从可证伪问题开始，而不是从材料数量开始", "同一研究案例承载历史与当前工作；截止日限定可见证据，冻结快照保留当时判断。")}
        <div class="overview-grid">
          <article class="paper-card">
            <div class="meta-row">
              <strong>${escapeHTML(activeCase.id)}</strong>
              <span>截止 ${escapeHTML(activeCase.cutoff)}</span>
              <span>快照 ${escapeHTML(activeCase.snapshotId)}</span>
            </div>
            <p class="case-question">${escapeHTML(activeCase.question)}</p>
            <div class="status-row">
              ${badge(`${reviewed} 条已复核`, "reviewed", "已复核状态")}
              ${badge(`${pending} 条待审核`, "warning", "待审核状态")}
              ${badge(data.case.aiLabel, "ai-draft", "人工复核前的 AI 草案")}
            </div>
            <div class="assessment" data-evidence-assessment>
              <strong>${escapeHTML(data.case.aiLabel)}</strong>
              <p>${escapeHTML(activeCase.provisionalAssessment)}</p>
            </div>
          </article>
          <aside class="paper-card">
            <h2>下一步验证</h2>
            <div class="mini-stack">
              ${data.theses.map((thesis) => `
                <div class="mini-item">
                  <h3>${escapeHTML(thesis.title)}</h3>
                  <p>${escapeHTML(thesis.nextValidationEvent)}</p>
                </div>
              `).join("")}
            </div>
          </aside>
        </div>
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
