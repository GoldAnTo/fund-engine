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

  const PRESENTATION = Object.freeze({
    caseStates: Object.freeze({
      awaiting_validation: "持续验证中",
    }),
    reviewStates: Object.freeze({
      pending_review: "待人工审核",
      reviewed: "已人工复核",
    }),
    factorStates: Object.freeze({
      candidate: "候选线索",
    }),
    providerOutcomes: Object.freeze({
      quota_failure: "配额受限",
      permission_gap: "权限缺口",
    }),
    providerNames: Object.freeze({
      juyuan: "聚源",
      "SEC EDGAR": "监管披露",
      "Issuer IR": "公司投资者关系披露",
      "Market data quota": "市场数据接口",
      "Licensed holdings feed": "持仓数据接口",
      "Research operations": "研究资料补录",
    }),
    providerDetails: Object.freeze({
      "Daily call limit exceeded; no inferred replacement values": "当日调用额度已用尽，未使用推测值替代。",
      "Current credential lacks historical holdings permission": "当前凭证缺少历史持仓读取权限。",
    }),
    metricNames: Object.freeze({
      "Data Center revenue": "数据中心收入",
    }),
    planMetricNames: Object.freeze({
      "Data Center revenue": "NVIDIA 数据中心业务收入",
      "May monthly revenue year-on-year change": "台积电月度营收同比增幅",
    }),
    metricValues: Object.freeze({
      "$39.1bn": "391 亿美元",
      "34.8%": "34.8%",
    }),
    metricPeriods: Object.freeze({
      "FY2026 Q1": "2026 财年第一季度",
      "2025-05": "2025 年 5 月",
    }),
    providerCapabilities: Object.freeze({
      industry_analysis_view: "行业分析观点",
      announcement_filing_fulltext: "公告财报原文",
      fund_holding_detail: "基金持股明细",
    }),
    queryModes: Object.freeze({
      capability_probe: "能力探测",
    }),
    planStates: Object.freeze({
      planned: "计划",
    }),
  });

  function displayLabel(labels, value, fallback) {
    return labels[value] ?? fallback;
  }

  function indexById(records) {
    return new Map(records.map((record) => [record.id, record]));
  }

  function resolveReviewWorkItem(fixture, reviewItem) {
    const statements = indexById(fixture.statements);
    const evidenceLinks = indexById(fixture.evidenceLinks);
    const theses = indexById(fixture.theses);
    const factors = indexById(fixture.factors);
    const directTargets = indexById([
      ...fixture.documents,
      ...fixture.statements,
      ...fixture.evidenceLinks,
      ...fixture.metrics,
      ...fixture.companies,
      ...fixture.funds,
      ...fixture.theses,
      ...fixture.factors,
    ]);
    const directTarget = directTargets.get(reviewItem.targetId);
    const evidence = evidenceLinks.get(reviewItem.targetId)
      ?? fixture.evidenceLinks.find((link) => link.statementId === reviewItem.targetId);
    const statement = statements.get(reviewItem.targetId)
      ?? (evidence ? statements.get(evidence.statementId) : undefined);
    const linkedTarget = evidence
      ? theses.get(evidence.thesisId) ?? factors.get(evidence.factorId)
      : undefined;
    const isFallback = !evidence || !linkedTarget;

    return {
      workItemId: reviewItem.id,
      targetId: reviewItem.targetId,
      task: reviewItem.task,
      blockerTitle: linkedTarget?.title ?? linkedTarget?.label ?? `待审核事项 ${reviewItem.id}`,
      sourceId: evidence?.id ?? statement?.id ?? directTarget?.id ?? reviewItem.targetId,
      sourceVersion: evidence?.sourceVersion ?? statement?.sourceVersion ?? directTarget?.sourceVersion ?? reviewItem.sourceVersion,
      reviewStatusLabel: displayLabel(PRESENTATION.reviewStates, reviewItem.reviewState, "状态待确认"),
      actionLabel: `审核：${reviewItem.task}`,
      actionRoute: `?screen=review&item=${encodeURIComponent(reviewItem.id)}`,
      isFallback,
      resolutionScore: Number(Boolean(statement)) + (Number(Boolean(evidence)) * 2) + (Number(Boolean(linkedTarget)) * 4),
    };
  }

  function buildOverviewViewModel(fixture) {
    const workItem = fixture.reviewQueue
      .map((item) => resolveReviewWorkItem(fixture, item))
      .sort((left, right) => right.resolutionScore - left.resolutionScore || left.workItemId.localeCompare(right.workItemId))[0];
    const contradiction = fixture.factors.find((item) => item.group === "contradiction");
    const metric = fixture.metrics.find((item) => item.id === "M-NVDA-DC-REV") ?? fixture.metrics[0];
    const recentSnapshot = fixture.snapshots.find((item) => item.id === fixture.case.snapshotId);
    const hasPriorMetricVersion = metric.snapshotMembership.some((snapshotId) => snapshotId !== fixture.case.snapshotId);
    const providers = fixture.providerRuns
      .filter((item) => ["quota_failure", "permission_gap"].includes(item.outcome))
      .map((run) => ({
        id: run.id,
        displayName: displayLabel(PRESENTATION.providerNames, run.provider, "外部数据接口"),
        outcomeLabel: displayLabel(PRESENTATION.providerOutcomes, run.outcome, "运行异常"),
        detailLabel: displayLabel(PRESENTATION.providerDetails, run.detail, "提供方返回未分类错误。"),
      }));

    return {
      case: fixture.case,
      caseStateLabel: displayLabel(PRESENTATION.caseStates, fixture.case.state, "案例状态待确认"),
      workItem,
      contradiction: {
        id: contradiction.id,
        label: contradiction.label,
        stateLabel: displayLabel(PRESENTATION.factorStates, contradiction.status, "线索状态待确认"),
      },
      metric: {
        id: metric.id,
        displayName: displayLabel(PRESENTATION.metricNames, metric.name, "关键业务指标"),
        value: metric.value,
        period: metric.period,
        sourceVersion: metric.sourceVersion,
        gapLabel: hasPriorMetricVersion ? "已有跨版本口径" : "缺少前次快照对照",
      },
      providers,
      recentSnapshot,
    };
  }

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

  function renderNavLinks(activeNav) {
    return NAV_ITEMS.map((item) => `
      <a class="nav-link" href="?screen=${escapeHTML(item.screen)}"${item.screen === activeNav ? ' aria-current="page"' : ""}>
        <span class="nav-icon" aria-hidden="true">${escapeHTML(item.icon)}</span>
        <span>${escapeHTML(item.label)}</span>
      </a>
    `).join("");
  }

  function renderOverview() {
    const view = buildOverviewViewModel(data);
    const activeCase = view.case;

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

          <ol class="case-list" aria-label="ResearchCase 队列">
          <li class="case-row selected" data-research-case-row data-current-case>
            <span class="sr-only">当前研究案例</span>
            <div class="case-main">
              <div class="case-identity">
                <span class="case-id">${escapeHTML(activeCase.id)}</span>
                ${badge(view.caseStateLabel, "warning", `案例状态：${view.caseStateLabel}`)}
              </div>
              <h3>${escapeHTML(activeCase.title)}</h3>
              <p class="case-question">${escapeHTML(activeCase.question)}</p>
              <dl class="case-facts">
                <div><dt>截止日</dt><dd>${escapeHTML(activeCase.cutoff)}</dd></div>
                <div><dt>当前快照</dt><dd>${escapeHTML(activeCase.snapshotId)}</dd></div>
                <div><dt>案例状态</dt><dd data-state-label>${escapeHTML(view.caseStateLabel)}</dd></div>
                <div><dt>关系审核状态</dt><dd data-state-label>${escapeHTML(view.workItem.reviewStatusLabel)}</dd></div>
              </dl>
              <div class="assessment compact-assessment" data-evidence-assessment>
                <strong>${escapeHTML(activeCase.aiLabel)}</strong>
                <p>${escapeHTML(activeCase.provisionalAssessment)}</p>
              </div>
            </div>
            <div class="case-decision">
              <p class="decision-label">主要阻塞</p>
              <h4>${escapeHTML(view.workItem.blockerTitle)}</h4>
              <p>${escapeHTML(view.workItem.task)}</p>
              <div class="decision-source">
                <span>${escapeHTML(view.workItem.sourceId)}</span>
                <span>${escapeHTML(view.workItem.sourceVersion)}</span>
              </div>
              <a class="next-action" data-next-action href="${escapeHTML(view.workItem.actionRoute)}">${escapeHTML(view.workItem.actionLabel)} <span aria-hidden="true">→</span></a>
            </div>
          </li>
          </ol>
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
            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(view.workItem.workItemId)}">
              <div class="lane-heading"><span class="lane-mark warning" aria-hidden="true">审</span><h3>待审核关系</h3></div>
              <p class="lane-state" data-state-label>${escapeHTML(view.workItem.reviewStatusLabel)}</p>
              <strong>${escapeHTML(view.workItem.task)}</strong>
              <p class="lane-detail">${escapeHTML(view.workItem.targetId)} · ${escapeHTML(view.workItem.sourceVersion)}</p>
            </article>

            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(view.contradiction.id)}">
              <div class="lane-heading"><span class="lane-mark contradict" aria-hidden="true">反</span><h3>新反面证据</h3></div>
              <p class="lane-state" data-state-label>${escapeHTML(view.contradiction.stateLabel)} · 待关联来源</p>
              <strong>${escapeHTML(view.contradiction.label)}</strong>
              <p class="lane-detail">${escapeHTML(view.contradiction.id)} · 未进入正式证据关系</p>
            </article>

            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(view.metric.id)}">
              <div class="lane-heading"><span class="lane-mark gap" aria-hidden="true">缺</span><h3>数据修订与缺口</h3></div>
              <p class="lane-state" data-state-label>${escapeHTML(view.metric.gapLabel)}</p>
              <strong>${escapeHTML(view.metric.displayName)} · ${escapeHTML(view.metric.value)}</strong>
              <p class="lane-detail">${escapeHTML(view.metric.period)} · ${escapeHTML(view.metric.sourceVersion)}</p>
            </article>

            <article class="status-lane provider-lane" data-support-lane data-source-id="${escapeHTML(view.providers.map((item) => item.id).join(","))}">
              <div class="lane-heading"><span class="lane-mark provider" aria-hidden="true">源</span><h3>Provider 状态</h3></div>
              ${view.providers.map((run) => `
                <div class="provider-item">
                  <p class="lane-state" data-state-label>${escapeHTML(run.outcomeLabel)}</p>
                  <strong>${escapeHTML(run.displayName)}</strong>
                  <p class="lane-detail">${escapeHTML(run.detailLabel)}</p>
                </div>
              `).join("")}
            </article>

            <article class="status-lane" data-support-lane data-source-id="${escapeHTML(view.recentSnapshot.id)}">
              <div class="lane-heading"><span class="lane-mark frozen" aria-hidden="true">冻</span><h3>最近冻结版本</h3></div>
              <p class="lane-state" data-state-label>${escapeHTML(view.recentSnapshot.label)} · 已冻结</p>
              <strong>${escapeHTML(view.recentSnapshot.id)}</strong>
              <p class="lane-detail">截止 ${escapeHTML(view.recentSnapshot.cutoff)}<br>冻结于 ${escapeHTML(view.recentSnapshot.frozenAt)}</p>
            </article>
          </div>
        </section>
      </section>
    `;
  }

  const CONFIRMATION_SCHEMA_VERSION = 1;
  const DRAFT_FIELDS = Object.freeze(["statement", "observationPeriod", "supportCondition", "falsifier", "nextValidationEvent"]);
  const RECOVERY_MESSAGE = "未找到已确认草稿，请重新确认初始命题";

  function confirmationStorageKey(caseId) {
    return `new-research-confirmation:v${CONFIRMATION_SCHEMA_VERSION}:${caseId}`;
  }

  function normalizeDraftText(value) {
    if (typeof value !== "string") return undefined;
    const normalized = value.trim();
    return normalized && normalized.length <= 2000 ? normalized : undefined;
  }

  function normalizeConfirmationRecord(candidate, fixture) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return undefined;
    if (candidate.schemaVersion !== CONFIRMATION_SCHEMA_VERSION || candidate.caseId !== fixture.case.id) return undefined;
    if (candidate.snapshotId !== fixture.case.snapshotId || candidate.cutoff !== fixture.case.cutoff) return undefined;
    if (candidate.researchPlanRevision !== fixture.case.researchPlan.revision) return undefined;
    if (!Array.isArray(candidate.theses) || candidate.theses.length < 1 || candidate.theses.length > 3) return undefined;

    const normalizedTheses = [];
    const observedIds = new Set();
    for (let index = 0; index < candidate.theses.length; index += 1) {
      const draft = candidate.theses[index];
      const id = normalizeDraftText(draft?.id);
      if (!id || !/^[A-Z0-9][A-Z0-9-]{2,63}$/u.test(id) || observedIds.has(id)) return undefined;
      observedIds.add(id);
      const normalized = { id };
      for (const field of DRAFT_FIELDS) {
        normalized[field] = normalizeDraftText(draft[field]);
        if (!normalized[field]) return undefined;
      }
      normalizedTheses.push(normalized);
    }
    return {
      schemaVersion: CONFIRMATION_SCHEMA_VERSION,
      caseId: fixture.case.id,
      snapshotId: fixture.case.snapshotId,
      cutoff: fixture.case.cutoff,
      researchPlanRevision: fixture.case.researchPlan.revision,
      theses: normalizedTheses,
    };
  }

  function readConfirmationRecord(fixture) {
    const key = confirmationStorageKey(fixture.case.id);
    try {
      const raw = window.sessionStorage.getItem(key);
      if (!raw) return undefined;
      const normalized = normalizeConfirmationRecord(JSON.parse(raw), fixture);
      if (!normalized) window.sessionStorage.removeItem(key);
      return normalized;
    } catch {
      try { window.sessionStorage.removeItem(key); } catch { /* Storage may be unavailable. */ }
      return undefined;
    }
  }

  function canonicalResearchURL(step) {
    return step === 3 ? "?screen=new-research&step=3" : "?screen=new-research";
  }

  function resolveNewResearchState(fixture) {
    const params = new URLSearchParams(window.location.search);
    const stepValues = params.getAll("step");
    const requestsStepThree = stepValues.length === 1 && stepValues[0] === "3";
    const confirmation = readConfirmationRecord(fixture);
    const activeStep = requestsStepThree && confirmation ? 3 : 2;
    const recoveryMessage = requestsStepThree && !confirmation ? RECOVERY_MESSAGE : undefined;
    const canonicalURL = canonicalResearchURL(activeStep);
    if (window.location.search !== canonicalURL) window.history.replaceState(null, "", canonicalURL);
    return { activeStep, confirmation, recoveryMessage };
  }

  function formatPlannedProviderQuery(query) {
    const provider = displayLabel(PRESENTATION.providerNames, query.provider, "外部数据接口");
    const capability = displayLabel(PRESENTATION.providerCapabilities, query.capability, "未分类能力");
    const mode = displayLabel(PRESENTATION.queryModes, query.mode, "读取探测");
    const status = displayLabel(PRESENTATION.planStates, query.status, "待规划");
    return `${provider} · ${capability} · ${mode}（${status}）`;
  }

  function formatPlannedMetric(metric) {
    return [
      displayLabel(PRESENTATION.planMetricNames, metric.name, "关键业务指标"),
      displayLabel(PRESENTATION.metricValues, metric.value, metric.value),
      displayLabel(PRESENTATION.metricPeriods, metric.period, metric.period),
    ].join(" · ");
  }

  function buildNewResearchViewModel(fixture, requestedStep = 2, confirmation) {
    const activeStep = requestedStep === 3 && confirmation ? 3 : 2;
    const plan = fixture.case.researchPlan;
    const documents = indexById(fixture.documents);
    const statements = indexById(fixture.statements);
    const metrics = indexById(fixture.metrics);
    const evidenceLinks = indexById(fixture.evidenceLinks);
    const factors = indexById(fixture.factors);
    const studyRange = `${fixture.case.researchPeriod.start} 至 ${fixture.case.researchPeriod.end}`;
    const fixtureTheses = indexById(fixture.theses);
    const thesisDrafts = confirmation?.theses ?? fixture.theses.map((thesis) => ({
      id: thesis.id,
      statement: thesis.statement,
      observationPeriod: studyRange,
      supportCondition: thesis.supportCondition,
      falsifier: thesis.falsifier,
      nextValidationEvent: thesis.nextValidationEvent,
    }));
    const theses = thesisDrafts.map((draft, index) => ({
      ...fixtureTheses.get(draft.id),
      title: fixtureTheses.get(draft.id)?.title ?? `新增命题 ${index + 1}`,
      ...draft,
    }));

    return {
      case: fixture.case,
      activeStep,
      confirmation,
      stageStatus: activeStep === 3 ? "当前阶段 · 复用资产待确认" : "当前阶段 · 命题待人工确认",
      researchPeriod: fixture.case.researchPeriod,
      studyRange,
      researchObject: fixture.case.researchObject,
      phenomenon: fixture.case.phenomenon,
      theses,
      assets: {
        documents: plan.reusableAssets.documentIds.map((id) => documents.get(id)),
        statements: plan.reusableAssets.statementIds.map((id) => statements.get(id)),
        metrics: plan.reusableAssets.metricIds.map((id) => metrics.get(id)),
        reviewedLinks: plan.reusableAssets.evidenceLinkIds.map((id) => evidenceLinks.get(id)),
        relatedCases: plan.reusableAssets.relatedCaseIds,
      },
      plan: {
        providerQueries: plan.plannedProviderQueries.map(formatPlannedProviderQuery),
        positiveEvidenceSearches: plan.positiveEvidenceSearches,
        negativeEvidenceSearches: plan.negativeEvidenceSearches,
        resultData: plan.resultMetricIds.map((id) => formatPlannedMetric(metrics.get(id))),
        gaps: plan.currentGapFactorIds.map((id) => factors.get(id)),
      },
    };
  }

  function renderThesisEditor(thesis, index, aiLabel) {
    const fieldPrefix = `thesis-${thesis.id.toLowerCase()}`;
    return `
      <fieldset class="thesis-editor" data-thesis-editor data-thesis-id="${escapeHTML(thesis.id)}">
        <legend><span data-thesis-number>命题 ${index + 1}</span>${escapeHTML(thesis.title)}</legend>
        <div class="thesis-editor-tools">
          <span class="ai-suggestion-label" data-ai-suggestion-label>${escapeHTML(aiLabel)}</span>
          <button class="remove-thesis-action" type="button" data-remove-thesis aria-label="删除命题 ${index + 1}" aria-describedby="thesis-minimum-description">删除</button>
        </div>
        <div class="thesis-fields">
          <label class="statement-field" for="${fieldPrefix}-statement">
            <span>命题表述</span>
            <textarea id="${fieldPrefix}-statement" data-field="statement" rows="2">${escapeHTML(thesis.statement)}</textarea>
          </label>
          <label for="${fieldPrefix}-period">
            <span>观察期间</span>
            <input id="${fieldPrefix}-period" data-field="observationPeriod" value="${escapeHTML(thesis.observationPeriod)}">
          </label>
          <label class="event-field" for="${fieldPrefix}-event">
            <span>下一验证事件</span>
            <textarea id="${fieldPrefix}-event" data-field="nextValidationEvent" rows="2">${escapeHTML(thesis.nextValidationEvent)}</textarea>
          </label>
          <label for="${fieldPrefix}-support">
            <span>支持条件</span>
            <textarea id="${fieldPrefix}-support" data-field="supportCondition" rows="2">${escapeHTML(thesis.supportCondition)}</textarea>
          </label>
          <label for="${fieldPrefix}-falsifier">
            <span>反证条件</span>
            <textarea id="${fieldPrefix}-falsifier" data-field="falsifier" rows="2">${escapeHTML(thesis.falsifier)}</textarea>
          </label>
        </div>
      </fieldset>
    `;
  }

  function researchStepState(step, currentStep) {
    if (step < currentStep) return "completed";
    if (step === currentStep) return "current";
    return "upcoming";
  }

  function renderResearchStep(label, step, currentStep) {
    const state = researchStepState(step, currentStep);
    return `<li data-step-state="${state}"${state === "current" ? ' aria-current="step"' : ""}>${escapeHTML(label)}</li>`;
  }

  function renderThesisStage(view, currentStep) {
    if (currentStep > 2) {
      return `
        <section class="thesis-complete-summary" aria-labelledby="confirmed-theses-title">
          <div><p>第 2 步 · 已完成</p><h2 id="confirmed-theses-title">初始命题已确认</h2></div>
          <ol data-confirmed-theses>${view.theses.map((thesis) => `
            <li>
              <strong>${escapeHTML(thesis.title)}</strong>
              <span>${escapeHTML(thesis.statement)}</span>
              <small>观察期间：${escapeHTML(thesis.observationPeriod)}</small>
              <small>支持条件：${escapeHTML(thesis.supportCondition)}</small>
              <small>反证条件：${escapeHTML(thesis.falsifier)}</small>
              <small>下一验证事件：${escapeHTML(thesis.nextValidationEvent)}</small>
            </li>
          `).join("")}</ol>
        </section>
      `;
    }

    return `
      <form class="thesis-form" aria-label="初始命题" method="get">
        <input type="hidden" name="screen" value="new-research">
        <input type="hidden" name="step" value="3">
        <div class="form-heading">
          <div><p>第 2 步 · 当前</p><h2>初始命题</h2></div>
          <div class="form-guidance">
            <strong>初始命题支持 1–3 条</strong>
            <p>先写清什么会支持，什么会推翻，以及下次去哪里验证。</p>
          </div>
        </div>
        <div class="thesis-editors">
          ${view.theses.map((thesis, index) => renderThesisEditor(thesis, index, view.case.aiLabel)).join("")}
        </div>
        <div class="new-research-form-actions">
          <div class="thesis-count-guidance">
            <p class="thesis-limit-note" id="thesis-limit-description">已达 3 条上限；删除后可新增</p>
            <p class="thesis-minimum-note" id="thesis-minimum-description" hidden>至少保留 1 条初始命题</p>
          </div>
          <button class="new-research-secondary-action" type="button">AI 协助拆分</button>
          <button class="new-research-secondary-action" type="button" data-add-thesis disabled aria-describedby="thesis-limit-description">新增命题</button>
          <button class="primary-action" data-primary-action type="submit">确认命题并继续</button>
        </div>
        <p class="form-error" data-form-error role="alert" hidden></p>
      </form>
    `;
  }

  function renderNewResearch() {
    const state = resolveNewResearchState(data);
    const view = buildNewResearchViewModel(data, state.activeStep, state.confirmation);
    const currentStep = view.activeStep;
    const assetsAreCurrent = currentStep === 3;
    return `
      <main class="screen new-research-screen" data-screen="new-research">
        <header class="new-research-heading">
          <div>
            <p class="eyebrow">新建产业命题</p>
            <h1>新建产业研究</h1>
            <p class="lede">把一个行业判断拆成可验证、可反证的命题；当前仅确认初始命题，不代表系统已得出结论。</p>
          </div>
          <span class="draft-boundary" data-stage-status>${escapeHTML(view.stageStatus)}</span>
        </header>

        <nav class="step-navigation" aria-label="新建研究步骤">
          <ol data-research-steps>
            ${renderResearchStep("研究问题", 1, currentStep)}
            ${renderResearchStep("初始命题", 2, currentStep)}
            ${renderResearchStep("已有资产", 3, currentStep)}
            ${renderResearchStep("研究计划", 4, currentStep)}
          </ol>
        </nav>

        ${state.recoveryMessage ? `<p class="recovery-message" data-recovery-message role="status">${escapeHTML(state.recoveryMessage)}</p>` : ""}

        <section class="question-summary" data-question-summary aria-labelledby="question-summary-title">
          <div class="summary-title">
            <span class="step-status-mark" aria-hidden="true">✓</span>
            <div><p>第 1 步 · 已完成</p><h2 id="question-summary-title">研究问题摘要</h2></div>
          </div>
          <dl>
            <div class="summary-question"><dt>研究名称</dt><dd>${escapeHTML(view.case.title)}</dd></div>
            <div class="summary-question"><dt>核心问题</dt><dd>${escapeHTML(view.case.question)}</dd></div>
            <div><dt>研究对象</dt><dd>${escapeHTML(view.researchObject)}</dd></div>
            <div><dt>待解释现象</dt><dd>${escapeHTML(view.phenomenon)}</dd></div>
            <div><dt>研究时间范围</dt><dd data-summary-field="research-range">${escapeHTML(view.studyRange)}</dd></div>
            <div><dt>证据截止日</dt><dd data-summary-field="evidence-cutoff">仅纳入 ${escapeHTML(view.case.cutoff)} 当日及之前可用证据</dd></div>
          </dl>
        </section>

        ${renderThesisStage(view, currentStep)}

        <div class="step-previews${assetsAreCurrent ? " has-current-stage" : ""}">
          <section class="${assetsAreCurrent ? "current-step-stage" : ""}" data-step-stage="assets"${assetsAreCurrent ? " data-step-current" : ' data-step-preview="assets"'} aria-labelledby="assets-preview-title">
            <header><div><p>第 3 步${assetsAreCurrent ? " · 当前" : ""}</p><h2 id="assets-preview-title">已有资产</h2></div>${assetsAreCurrent ? '<span data-current-stage>当前阶段 · 选择可复用资产</span>' : '<span data-preview-state>尚未完成 · 下一步预览</span>'}</header>
            <ul>
              <li><strong>可复用文档</strong><span>${view.assets.documents.length} 份</span></li>
              <li><strong>可复用陈述</strong><span>${view.assets.statements.length} 条</span></li>
              <li><strong>可复用数据</strong><span>${view.assets.metrics.length} 项</span></li>
              <li><strong>已复核关系</strong><span>${view.assets.reviewedLinks.length} 条</span></li>
              <li><strong>相关案例资产</strong><span>${escapeHTML(view.assets.relatedCases.join("、"))}</span></li>
            </ul>
          </section>
          <section data-step-preview="plan" aria-labelledby="plan-preview-title">
            <header><div><p>第 4 步</p><h2 id="plan-preview-title">研究计划</h2></div><span data-preview-state>尚未完成 · 下一步预览</span></header>
            <ul>
              <li data-plan-item="reuse"><strong>计划内部复用</strong><span>文档、陈述与已复核关系</span></li>
              <li data-plan-item="providers"><strong>提供方查询</strong><span>${view.plan.providerQueries.map((query) => escapeHTML(query)).join("；")}</span></li>
              <li data-plan-item="evidence"><strong>正面与反面证据搜索</strong><span>正面：${escapeHTML(view.plan.positiveEvidenceSearches.join("、"))}<br>反面：${escapeHTML(view.plan.negativeEvidenceSearches.join("、"))}</span></li>
              <li data-plan-item="metrics"><strong>结果数据</strong><span>${view.plan.resultData.map((metric) => escapeHTML(metric)).join("；")}</span></li>
              <li data-plan-item="gaps"><strong>当前缺口</strong><span>${escapeHTML(view.plan.gaps.map((gap) => gap.label).join("、"))}</span></li>
            </ul>
          </section>
        </div>
      </main>
    `;
  }

  function collectConfirmationRecord(form, fixture) {
    const theses = [...form.querySelectorAll("[data-thesis-editor]")].map((editor) => {
      const draft = { id: editor.dataset.thesisId };
      for (const field of DRAFT_FIELDS) {
        draft[field] = editor.querySelector(`[data-field="${field}"]`)?.value;
      }
      return draft;
    });
    return normalizeConfirmationRecord({
      schemaVersion: CONFIRMATION_SCHEMA_VERSION,
      caseId: fixture.case.id,
      snapshotId: fixture.case.snapshotId,
      cutoff: fixture.case.cutoff,
      researchPlanRevision: fixture.case.researchPlan.revision,
      theses,
    }, fixture);
  }

  function applyConfirmationRecordToForm(form, record) {
    const drafts = new Map(record.theses.map((thesis) => [thesis.id, thesis]));
    for (const editor of form.querySelectorAll("[data-thesis-editor]")) {
      const draft = drafts.get(editor.dataset.thesisId);
      if (!draft) continue;
      for (const field of DRAFT_FIELDS) editor.querySelector(`[data-field="${field}"]`).value = draft[field];
    }
  }

  function updateThesisEditorControls(form) {
    const editors = [...form.querySelectorAll("[data-thesis-editor]")];
    const addButton = form.querySelector("[data-add-thesis]");
    const limitNote = form.querySelector("#thesis-limit-description");
    const minimumNote = form.querySelector("#thesis-minimum-description");
    editors.forEach((editor, index) => {
      editor.querySelector("[data-thesis-number]").textContent = `命题 ${index + 1}`;
      const removeButton = editor.querySelector("[data-remove-thesis]");
      removeButton.disabled = editors.length === 1;
      removeButton.setAttribute("aria-label", `删除命题 ${index + 1}`);
    });
    addButton.disabled = editors.length >= 3;
    limitNote.textContent = editors.length >= 3
      ? "已达 3 条上限；删除后可新增"
      : `当前 ${editors.length} 条；可新增至 3 条`;
    minimumNote.hidden = editors.length !== 1;
  }

  function appendBlankThesisEditor(form, fixture) {
    const observedIds = new Set([...form.querySelectorAll("[data-thesis-editor]")].map((editor) => editor.dataset.thesisId));
    let sequence = Number(form.dataset.nextDraftSequence ?? 1);
    let id = `TH-DRAFT-${sequence}`;
    while (observedIds.has(id)) {
      sequence += 1;
      id = `TH-DRAFT-${sequence}`;
    }
    form.dataset.nextDraftSequence = String(sequence + 1);
    const blankDraft = {
      id,
      title: "新增命题",
      statement: "",
      observationPeriod: "",
      supportCondition: "",
      falsifier: "",
      nextValidationEvent: "",
    };
    const container = form.querySelector(".thesis-editors");
    container.insertAdjacentHTML("beforeend", renderThesisEditor(blankDraft, container.children.length, fixture.case.aiLabel));
    updateThesisEditorControls(form);
    container.lastElementChild.querySelector('[data-field="statement"]').focus();
  }

  function bindNewResearchForm(fixture) {
    const form = app.querySelector('.new-research-screen form[aria-label="初始命题"]');
    if (!form) return;
    updateThesisEditorControls(form);
    form.addEventListener("click", (event) => {
      const removeButton = event.target.closest("[data-remove-thesis]");
      if (removeButton) {
        if (removeButton.disabled || form.querySelectorAll("[data-thesis-editor]").length <= 1) return;
        removeButton.closest("[data-thesis-editor]").remove();
        updateThesisEditorControls(form);
        form.querySelector("[data-add-thesis]").focus();
        return;
      }
      const addButton = event.target.closest("[data-add-thesis]");
      if (addButton && !addButton.disabled) appendBlankThesisEditor(form, fixture);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const error = form.querySelector("[data-form-error]");
      const record = collectConfirmationRecord(form, fixture);
      if (!record) {
        error.hidden = false;
        error.textContent = "请完整填写 1–3 条命题的命题表述、观察期间、支持条件、反证条件与下一验证事件。";
        return;
      }
      try {
        window.sessionStorage.setItem(confirmationStorageKey(fixture.case.id), JSON.stringify(record));
      } catch {
        error.hidden = false;
        error.textContent = "当前会话无法保存草稿，请检查浏览器存储设置后重试。";
        return;
      }
      applyConfirmationRecordToForm(form, record);
      window.location.assign(canonicalResearchURL(3));
    });
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
    "new-research": renderNewResearch,
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
    const activeNav = NAV_ITEMS.find((item) => item.screen === screen)?.screen;
    app.innerHTML = `
      <div class="app-shell">
        <aside class="nav-rail" aria-label="主导航">
          <div class="brand">
            <span class="brand-mark" aria-hidden="true">R</span>
            <div><strong>研究台账</strong><small>EVIDENCE LEDGER</small></div>
          </div>
          <nav class="nav-list">
            ${renderNavLinks(activeNav)}
          </nav>
          <p class="nav-note">证据台账为事实源<br>图谱与搜索均为可重建投影</p>
        </aside>
        <header class="utility-header">
          <div class="breadcrumbs"><span>AI 算力产业链</span><span aria-hidden="true">/</span><strong>${escapeHTML(data.case.snapshotId)}</strong></div>
          <div class="utility-actions"><span class="utility-pill">截止 ${escapeHTML(data.case.cutoff)}</span><span class="utility-pill">只读原型</span></div>
        </header>
        <details class="mobile-nav">
          <summary>导航</summary>
          <nav class="mobile-nav-links" aria-label="移动端主导航">
            ${renderNavLinks(activeNav)}
          </nav>
        </details>
        <div class="work-area">${SCREEN_RENDERERS[screen]()}</div>
      </div>
    `;
    if (screen === "new-research") bindNewResearchForm(data);
  }

  renderShell(requestedScreen());
  window.SCREEN_RENDERERS = SCREEN_RENDERERS;
  window.PROTOTYPE_OVERVIEW = Object.freeze({ buildOverviewViewModel });
  window.PROTOTYPE_NEW_RESEARCH = Object.freeze({ buildNewResearchViewModel, confirmationStorageKey });
}());
