(function () {
  "use strict";

  const data = window.PROTOTYPE_DATA;
  const researchState = window.NEW_RESEARCH_STATE;
  const planState = window.RESEARCH_PLAN_STATE;
  const caseState = window.CASE_WORKBENCH_STATE;
  const versionsState = window.VERSIONS_STATE;
  const {
    DRAFT_FIELDS,
    FIELD_LIMITS,
    confirmationStorageKey,
    createConfirmationRecord,
    evidenceReviewStateForDraft,
    readConfirmationRecord,
  } = researchState;
  const app = document.querySelector("#app");
  let teardownNewResearchAutosize = () => {};

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
      manual_upload: "人工补录",
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
      "Fund reports uploaded and queued for review": "基金报告曾由人工补录，并进入审核队列。",
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
    exposureStates: Object.freeze({
      probe_required: "尚待探测是否实际暴露并获授权",
    }),
    assetKinds: Object.freeze({
      document: "冻结文档",
      statement: "来源陈述",
      metric: "结果数据",
      evidence_link: "已审核关系",
    }),
    reviewStatesExtended: Object.freeze({
      reviewed: "已人工复核",
      pending_review: "待人工审核",
    }),
    gapTypes: Object.freeze({
      factor: "因素缺口",
      positive: "正面证据检索",
      negative: "反面证据检索",
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

  const RECOVERY_MESSAGE = "未找到已确认草稿，请重新确认初始命题";

  function canonicalResearchURL(step) {
    return step === 3 ? "?screen=new-research&step=3" : "?screen=new-research";
  }

  function resolveNewResearchState(fixture) {
    const params = new URLSearchParams(window.location.search);
    const stepValues = params.getAll("step");
    const requestsStepThree = stepValues.length === 1 && stepValues[0] === "3";
    const confirmation = readConfirmationRecord(window.sessionStorage, fixture);
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
      origin: thesis.origin,
      lastEditedBy: "ai",
      title: thesis.title,
      statement: thesis.statement,
      observationStart: thesis.observationStart,
      observationEnd: thesis.observationEnd,
      supportCondition: thesis.supportCondition,
      falsifier: thesis.falsifier,
      nextValidationEvent: thesis.nextValidationEvent,
    }));
    const theses = thesisDrafts.map((draft) => {
      const trustedFixture = fixtureTheses.get(draft.id);
      const evidenceLabels = {
        reviewed_links_present: "已有已审核关系",
        pending_relationship_review: "已有已审核关系 · 另有待审核关系",
        no_evidence_links: "尚无证据关系",
      };
      return {
        ...trustedFixture,
        ...draft,
        wasConfirmed: Boolean(confirmation),
        evidenceReviewLabel: evidenceLabels[evidenceReviewStateForDraft(draft.id, fixture)],
      };
    });

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
    const isAiDraft = thesis.origin === "ai";
    const originLabel = thesis.wasConfirmed
      ? (isAiDraft
        ? (thesis.lastEditedBy === "human" ? "AI 起草 · 人工已修改 · 待重新确认" : "AI 起草 · 已确认过 · 待重新确认")
        : "人工起草 · 已确认过 · 待重新确认")
      : (isAiDraft ? aiLabel : "人工草稿 · 待确认");
    const fieldError = (field) => `<p class="draft-field-error" id="${fieldPrefix}-${field}-error" data-field-error="${field}" hidden></p>`;
    return `
      <fieldset class="thesis-editor" data-thesis-editor data-thesis-id="${escapeHTML(thesis.id)}" data-origin="${escapeHTML(thesis.origin)}" data-was-confirmed="${thesis.wasConfirmed ? "true" : "false"}">
        <legend><span data-thesis-number>命题 ${index + 1}</span></legend>
        <div class="thesis-editor-tools">
          <span class="draft-origin-label" data-draft-origin-label${isAiDraft ? " data-ai-suggestion-label" : ""}>${escapeHTML(originLabel)}</span>
          <button class="remove-thesis-action" type="button" data-remove-thesis aria-label="删除命题 ${index + 1}" aria-describedby="thesis-minimum-description">删除</button>
        </div>
        <div class="thesis-fields">
          <label class="title-field" for="${fieldPrefix}-title">
            <span>命题标题</span>
            <input id="${fieldPrefix}-title" data-field="title" value="${escapeHTML(thesis.title)}" maxlength="${FIELD_LIMITS.title}" aria-describedby="${fieldPrefix}-title-error">
            ${fieldError("title")}
          </label>
          <label class="statement-field" for="${fieldPrefix}-statement">
            <span>命题表述</span>
            <textarea id="${fieldPrefix}-statement" data-field="statement" rows="2" maxlength="${FIELD_LIMITS.statement}" aria-describedby="${fieldPrefix}-statement-error">${escapeHTML(thesis.statement)}</textarea>
            ${fieldError("statement")}
          </label>
          <label class="date-field" for="${fieldPrefix}-observation-start">
            <span>观察开始</span>
            <input id="${fieldPrefix}-observation-start" type="date" data-field="observationStart" value="${escapeHTML(thesis.observationStart)}" aria-describedby="${fieldPrefix}-observationStart-error">
            ${fieldError("observationStart")}
          </label>
          <label class="date-field" for="${fieldPrefix}-observation-end">
            <span>观察结束</span>
            <input id="${fieldPrefix}-observation-end" type="date" data-field="observationEnd" value="${escapeHTML(thesis.observationEnd)}" aria-describedby="${fieldPrefix}-observationEnd-error">
            ${fieldError("observationEnd")}
          </label>
          <label class="event-field" for="${fieldPrefix}-event">
            <span>下一验证事件</span>
            <textarea id="${fieldPrefix}-event" data-field="nextValidationEvent" rows="2" maxlength="${FIELD_LIMITS.nextValidationEvent}" aria-describedby="${fieldPrefix}-nextValidationEvent-error">${escapeHTML(thesis.nextValidationEvent)}</textarea>
            ${fieldError("nextValidationEvent")}
          </label>
          <label for="${fieldPrefix}-support">
            <span>支持条件</span>
            <textarea id="${fieldPrefix}-support" data-field="supportCondition" rows="2" maxlength="${FIELD_LIMITS.supportCondition}" aria-describedby="${fieldPrefix}-supportCondition-error">${escapeHTML(thesis.supportCondition)}</textarea>
            ${fieldError("supportCondition")}
          </label>
          <label for="${fieldPrefix}-falsifier">
            <span>反证条件</span>
            <textarea id="${fieldPrefix}-falsifier" data-field="falsifier" rows="2" maxlength="${FIELD_LIMITS.falsifier}" aria-describedby="${fieldPrefix}-falsifier-error">${escapeHTML(thesis.falsifier)}</textarea>
            ${fieldError("falsifier")}
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

  function confirmedDraftLabel(thesis) {
    if (thesis.origin === "human") return "人工起草 · 已确认";
    return thesis.lastEditedBy === "human" ? "AI 起草 · 人工修改并确认" : "AI 起草 · 人工已确认";
  }

  function renderThesisStage(view, currentStep) {
    if (currentStep > 2) {
      return `
        <section class="thesis-complete-summary" aria-labelledby="confirmed-theses-title">
          <div><p>第 2 步 · 已完成</p><h2 id="confirmed-theses-title">初始命题已确认</h2><small class="confirmation-boundary">命题确认不等于证据关系已审核</small></div>
          <ol data-confirmed-theses>${view.theses.map((thesis) => `
            <li>
              <strong>${escapeHTML(thesis.title)}</strong>
              <span>${escapeHTML(thesis.statement)}</span>
              <small data-draft-origin-label>${escapeHTML(confirmedDraftLabel(thesis))}</small>
              <small data-evidence-review-state>证据关系：${escapeHTML(thesis.evidenceReviewLabel)}</small>
              <small>观察期间：${escapeHTML(thesis.observationStart)} 至 ${escapeHTML(thesis.observationEnd)}</small>
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
            <p class="ai-assist-note" id="ai-assist-description">当前原型展示既有 AI 拆分结果，不执行重新生成</p>
          </div>
          <button class="new-research-secondary-action" type="button" disabled aria-describedby="ai-assist-description">AI 协助拆分</button>
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

  function collectDrafts(form) {
    const theses = [...form.querySelectorAll("[data-thesis-editor]")].map((editor) => {
      const draft = { id: editor.dataset.thesisId, origin: editor.dataset.origin };
      for (const field of DRAFT_FIELDS) {
        draft[field] = editor.querySelector(`[data-field="${field}"]`)?.value;
      }
      return draft;
    });
    return theses;
  }

  function applyConfirmationRecordToForm(form, record) {
    const drafts = new Map(record.theses.map((thesis) => [thesis.id, thesis]));
    for (const editor of form.querySelectorAll("[data-thesis-editor]")) {
      const draft = drafts.get(editor.dataset.thesisId);
      if (!draft) continue;
      for (const field of DRAFT_FIELDS) editor.querySelector(`[data-field="${field}"]`).value = draft[field];
    }
    autoSizeThesisTextareas(form);
  }

  function autoSizeThesisTextarea(textarea) {
    textarea.style.height = "auto";
    const borderHeight = textarea.offsetHeight - textarea.clientHeight;
    textarea.style.height = `${textarea.scrollHeight + borderHeight}px`;
  }

  function autoSizeThesisTextareas(root) {
    for (const textarea of root.querySelectorAll("[data-thesis-editor] textarea")) autoSizeThesisTextarea(textarea);
  }

  function bindResponsiveTextareaAutosize(form) {
    const container = form.querySelector(".thesis-editors");
    let lastWidth = container.getBoundingClientRect().width;
    let animationFrame;
    const schedule = () => {
      if (animationFrame !== undefined) return;
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = undefined;
        autoSizeThesisTextareas(form);
      });
    };
    const widthChanged = (width) => {
      if (Math.abs(width - lastWidth) < 0.5) return;
      lastWidth = width;
      schedule();
    };

    if (typeof window.ResizeObserver === "function") {
      const observer = new window.ResizeObserver((entries) => {
        const entry = entries.find((item) => item.target === container);
        if (entry) widthChanged(entry.contentRect.width);
      });
      observer.observe(container);
      return () => {
        observer.disconnect();
        if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame);
      };
    }

    const onResize = () => widthChanged(container.getBoundingClientRect().width);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame);
    };
  }

  const DRAFT_ERROR_MESSAGES = Object.freeze({
    required: "此字段为必填项",
    invalid_date: "请输入真实有效的日期",
    reversed_range: "开始日期不能晚于结束日期",
    before_research_period: "开始日期不能早于研究范围",
    after_research_period: "结束日期不能晚于研究范围",
  });

  function clearDraftValidation(form) {
    for (const control of form.querySelectorAll("[data-field]")) control.removeAttribute("aria-invalid");
    for (const message of form.querySelectorAll("[data-field-error]")) {
      message.hidden = true;
      message.textContent = "";
    }
  }

  function showDraftValidation(form, errors) {
    clearDraftValidation(form);
    let firstInvalid;
    const editors = [...form.querySelectorAll("[data-thesis-editor]")];
    for (const [index, fieldErrors] of Object.entries(errors)) {
      if (index === "_record") continue;
      const editor = editors[Number(index)];
      if (!editor) continue;
      for (const [field, code] of Object.entries(fieldErrors)) {
        const control = editor.querySelector(`[data-field="${field}"]`);
        const message = editor.querySelector(`[data-field-error="${field}"]`);
        if (!control || !message) continue;
        control.setAttribute("aria-invalid", "true");
        message.textContent = code === "too_long"
          ? `内容超过允许长度（最多 ${FIELD_LIMITS[field]} 个字符）`
          : (DRAFT_ERROR_MESSAGES[code] ?? "此字段无效");
        message.hidden = false;
        firstInvalid ??= control;
      }
    }
    autoSizeThesisTextareas(form);
    firstInvalid?.focus();
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
      origin: "human",
      title: "",
      statement: "",
      observationStart: "",
      observationEnd: "",
      supportCondition: "",
      falsifier: "",
      nextValidationEvent: "",
    };
    const container = form.querySelector(".thesis-editors");
    container.insertAdjacentHTML("beforeend", renderThesisEditor(blankDraft, container.children.length, fixture.case.aiLabel));
    updateThesisEditorControls(form);
    autoSizeThesisTextareas(container.lastElementChild);
    container.lastElementChild.querySelector('[data-field="title"]').focus();
  }

  function bindNewResearchForm(fixture) {
    const form = app.querySelector('.new-research-screen form[aria-label="初始命题"]');
    if (!form) return;
    updateThesisEditorControls(form);
    autoSizeThesisTextareas(form);
    teardownNewResearchAutosize = bindResponsiveTextareaAutosize(form);
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
    form.addEventListener("input", (event) => {
      const control = event.target.closest("[data-field]");
      const editor = control?.closest("[data-thesis-editor]");
      if (!editor) return;
      if (control.matches("textarea")) autoSizeThesisTextarea(control);
      const originLabel = editor.querySelector("[data-draft-origin-label]");
      if (editor.dataset.origin === "ai") {
        originLabel.textContent = editor.dataset.wasConfirmed === "true"
          ? "AI 起草 · 人工已修改 · 待重新确认"
          : "AI 草案 · 人工已修改 · 待确认";
      } else if (editor.dataset.wasConfirmed === "true") {
        originLabel.textContent = "人工起草 · 已修改 · 待重新确认";
      }
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const error = form.querySelector("[data-form-error]");
      const result = createConfirmationRecord(collectDrafts(form), fixture);
      if (!result.record) {
        showDraftValidation(form, result.errors);
        error.hidden = false;
        error.textContent = "请修正已标记的命题字段后再确认。";
        return;
      }
      const record = result.record;
      clearDraftValidation(form);
      error.hidden = true;
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

  function buildReviewViewModel(fixture) {
    const statements = indexById(fixture.statements);
    const documents = indexById(fixture.documents);
    const theses = indexById(fixture.theses);
    const funds = indexById(fixture.funds);
    const requestedItem = new URLSearchParams(window.location.search).get("item");
    const selectedId = fixture.reviewQueue.some((item) => item.id === requestedItem)
      ? requestedItem
      : fixture.reviewQueue[0]?.id;
    const selectedQueueItem = fixture.reviewQueue.find((item) => item.id === selectedId);
    const priorityLabels = { high: "高", medium: "中", low: "低" };
    const reviewLabels = { pending_review: "待人工审核", reviewed: "已人工复核" };

    const queue = fixture.reviewQueue.map((item, index) => {
      const statement = statements.get(item.targetId);
      const fund = funds.get(item.targetId);
      const evidence = statement
        ? fixture.evidenceLinks.find((link) => link.statementId === statement.id)
        : undefined;
      const thesis = evidence ? theses.get(evidence.thesisId) : undefined;
      const document = statement ? documents.get(statement.documentId) : undefined;
      return {
        ...item,
        typeLabel: statement ? "来源陈述 · 关系" : "持仓披露映射",
        targetLabel: thesis ? `${thesis.id} · ${thesis.title}` : `${fund.id} · ${fund.name}`,
        sourceLabel: document?.title ?? `${fund.name} 2025 年一季报`,
        priorityLabel: priorityLabels[item.priority] ?? "待判定",
        reviewLabel: reviewLabels[item.reviewState] ?? "状态待确认",
        remainingLabel: `${fixture.reviewQueue.length - index} / ${fixture.reviewQueue.length}`,
        statement,
        document,
        evidence,
        thesis,
        fund,
      };
    });
    const selected = queue.find((item) => item.id === selectedId);
    const isStatementReview = Boolean(selected?.statement);
    const factor = fixture.factors.find((item) => item.id === "F-T-01");
    return {
      case: fixture.case,
      queue,
      selected: {
        ...selected,
        sourceExcerpt: selected?.statement?.sourceExcerpt
          ?? "Top ten holdings | TSMC | 6.1% | report date 2025-03-31",
        normalizedStatement: selected?.statement?.text
          ?? `该基金于 ${selected?.fund?.disclosureDate} 披露 ${selected?.fund?.name} 对台积电的持仓权重为 ${selected?.fund?.disclosedWeight}。`,
        targetThesis: selected?.thesis ? `${selected.thesis.id} · ${selected.thesis.title}` : "不适用 · 本项为披露映射",
        targetFactor: isStatementReview ? `${factor.id} · ${factor.label}` : "不适用 · 仅核对披露口径",
        proposedRelation: isStatementReview
          ? `${selected.statement.id} —[证据缺口]→ ${selected.thesis.id}`
          : `${selected.fund.id} —[披露映射]→ CO-TSM`,
      },
    };
  }

  function renderReviewQueueItem(item, selectedId) {
    const selected = item.id === selectedId;
    return `
      <li>
        <a class="review-queue-item${selected ? " is-selected" : ""}" data-review-queue-item data-selected="${selected}" href="?screen=review&amp;item=${escapeHTML(item.id)}" ${selected ? 'aria-current="true"' : ""}>
          <span class="review-item-top"><strong>${escapeHTML(item.id)}</strong><span class="review-priority ${escapeHTML(item.priority)}">优先级 · ${escapeHTML(item.priorityLabel)}</span></span>
          <span class="review-item-task">${escapeHTML(item.task)}</span>
          <dl>
            <div><dt>类型</dt><dd>${escapeHTML(item.typeLabel)}</dd></div>
            <div><dt>目标</dt><dd>${escapeHTML(item.targetLabel)}</dd></div>
            <div><dt>来源</dt><dd>${escapeHTML(item.sourceLabel)}</dd></div>
            <div><dt>审核状态</dt><dd>${escapeHTML(item.reviewLabel)}</dd></div>
          </dl>
          <span class="review-item-progress"><span>剩余进度</span><strong>${escapeHTML(item.remainingLabel)}</strong></span>
        </a>
      </li>
    `;
  }

  function renderReviewWorkbench() {
    const view = buildReviewViewModel(data);
    const item = view.selected;
    return `
      <main class="screen review-workbench-screen" data-screen="review">
        <header class="review-header">
          <div>
            <p class="eyebrow">Single-decision review</p>
            <h1>证据审核工作区</h1>
            <p class="lede">一次只审一个关系：对照冻结原文与 AI 提议，再由人明确选择关系、角色、边界和理由。</p>
          </div>
          <div class="review-snapshot"><span>当前审核基础</span><strong>${escapeHTML(view.case.snapshotId)}</strong><small>证据截止 ${escapeHTML(view.case.cutoff)} · 写入须人工确认</small></div>
        </header>

        <div class="review-workbench">
          <aside class="review-queue-panel" aria-labelledby="review-queue-title">
            <header><p>01 · 待办</p><h2 id="review-queue-title">审核队列</h2><span>${view.queue.length} 项待审</span></header>
            <ol>${view.queue.map((queueItem) => renderReviewQueueItem(queueItem, item.id)).join("")}</ol>
            <p class="review-queue-note">选中项已固定右侧写入目标；切换待办不会保留未提交的表单。</p>
          </aside>

          <section class="review-comparison" data-review-comparison aria-labelledby="review-comparison-title">
            <header><div><p>02 · 证据对照</p><h2 id="review-comparison-title">原文与规范化提议</h2></div><span class="source-lock">◇ 冻结来源版本</span></header>
            <article class="frozen-source" data-source-layer="frozen">
              <div class="layer-heading"><span class="layer-index">A</span><div><p>事实层 · 不可在本页改写</p><h3>冻结 SourceSpan</h3></div></div>
              <blockquote lang="en">${escapeHTML(item.sourceExcerpt)}</blockquote>
              <dl class="source-metadata">
                <div><dt>DocumentVersion</dt><dd>${escapeHTML(item.sourceVersion)}</dd></div>
                <div><dt>发布日期</dt><dd>${escapeHTML(item.publishedAt.slice(0, 10))}</dd></div>
                <div><dt>精确位置</dt><dd>${escapeHTML(item.sourceSpan)}</dd></div>
                <div><dt>可用时间</dt><dd>${escapeHTML(item.availableAt.replace("T", " "))}</dd></div>
                <div><dt>证据截止</dt><dd>${escapeHTML(view.case.cutoff)}</dd></div>
                <div><dt>冻结快照</dt><dd>${escapeHTML(view.case.snapshotId)}</dd></div>
              </dl>
            </article>

            <div class="normalization-step" aria-label="AI 规范化转换"><span>↓</span><strong>AI 抽取与规范化</strong><small>只生成候选陈述，不生成审核结论</small></div>

            <article class="ai-proposal" data-evidence-assessment data-source-layer="ai">
              <div class="layer-heading"><span class="layer-index">AI</span><div><p>提议层 · 不进入正式知识</p><h3>AI 规范化陈述</h3></div><strong class="ai-boundary">未经人工复核</strong></div>
              <p class="normalized-statement">${escapeHTML(item.normalizedStatement)}</p>
              <dl class="proposal-targets">
                <div><dt>目标 Thesis</dt><dd>${escapeHTML(item.targetThesis)}</dd></div>
                <div><dt>目标因素</dt><dd>${escapeHTML(item.targetFactor)}</dd></div>
                <div class="proposed-relation"><dt>拟建关系</dt><dd>${escapeHTML(item.proposedRelation)}</dd></div>
              </dl>
            </article>
          </section>

          <section class="human-decision" data-human-decision aria-labelledby="human-decision-title">
            <header><div><p>03 · 人工关口</p><h2 id="human-decision-title">审核人决定</h2></div><span class="human-only">只有人可写入</span></header>
            <p class="decision-boundary"><strong>AI 提议到此为止。</strong>以下四项必须由审核人明确填写，系统不预选。</p>
            <form data-review-form>
              <fieldset class="relation-field"><legend>1 · 关系选择 <span>必选</span></legend><div>
                <label><input type="radio" name="relation" value="support"><span>支持</span></label>
                <label><input type="radio" name="relation" value="contradict"><span>反驳</span></label>
                <label><input type="radio" name="relation" value="context"><span>背景</span></label>
                <label><input type="radio" name="relation" value="gap"><span>证据缺口</span></label>
              </div></fieldset>
              <label class="decision-field"><span>2 · 因素角色 <b>必选</b></span><select aria-label="因素角色" name="factor-role"><option value="">请人工选择</option><option value="demand">需求拉动</option><option value="supply">供给约束</option><option value="transmission">传导验证</option><option value="alternative">替代解释</option></select></label>
              <label class="decision-field"><span>3 · 适用边界 <b>必填</b></span><textarea aria-label="适用边界" name="boundary" rows="2" placeholder="例：仅适用于当前截止日与该分部口径"></textarea></label>
              <label class="decision-field"><span>4 · 审核理由 <b>必填</b></span><textarea aria-label="审核理由" name="rationale" rows="2" placeholder="写明为何接受、驳回或要求补证"></textarea></label>

              <section class="immutable-record" data-immutable-record aria-label="将写入的不可变记录">
                <header><div><p>写入预览</p><h3>追加新记录</h3></div><span data-review-gate>0 / 4 已完成</span></header>
                <dl><div><dt>对象</dt><dd>${escapeHTML(item.id)} · ${escapeHTML(item.targetId)}</dd></div><div><dt>绑定</dt><dd>${escapeHTML(item.sourceVersion)} · ${escapeHTML(view.case.snapshotId)}</dd></div><div><dt>将记录</dt><dd data-record-summary>关系、角色、边界、理由与审核时间</dd></div></dl>
                <p><strong>更正不覆盖：</strong>后续修正会追加新版本，原审核记录保留。</p>
              </section>

              <div class="review-actions" data-review-actions>
                <button type="submit" class="review-accept" disabled>确认并写入审核知识</button>
                <div><button type="button" class="review-reject" data-review-action="reject">驳回</button><button type="button" class="review-supplement" data-review-action="supplement">要求补充证据</button></div>
              </div>
              <p class="review-action-status" data-review-action-status aria-live="polite">完成四项人工判断后，确认动作才会解锁。</p>
            </form>
          </section>
        </div>
      </main>
    `;
  }

  function bindReviewWorkbench(root) {
    const form = root.querySelector("[data-review-form]");
    const accept = form.querySelector(".review-accept");
    const gate = form.querySelector("[data-review-gate]");
    const status = form.querySelector("[data-review-action-status]");
    const updateGate = () => {
      const values = [
        Boolean(form.querySelector('input[name="relation"]:checked')),
        Boolean(form.elements["factor-role"].value),
        Boolean(form.elements.boundary.value.trim()),
        Boolean(form.elements.rationale.value.trim()),
      ];
      const complete = values.filter(Boolean).length;
      gate.textContent = `${complete} / 4 已完成`;
      accept.disabled = complete !== 4;
      status.textContent = complete === 4
        ? "四项人工判断已齐备；请再次核对写入预览。"
        : "完成四项人工判断后，确认动作才会解锁。";
    };
    form.addEventListener("input", updateGate);
    form.addEventListener("change", updateGate);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (accept.disabled) return;
      status.textContent = "原型演示：将追加一条不可变审核记录；本页不连接真实写入。";
    });
    form.addEventListener("click", (event) => {
      const action = event.target.closest("[data-review-action]");
      if (!action) return;
      status.textContent = action.dataset.reviewAction === "reject"
        ? "原型演示：驳回会追加否决记录，不删除 AI 原提议。"
        : "原型演示：该项会留在队列中，并追加补证要求。";
    });
  }

  function buildLibraryViewModel(fixture) {
    const documentPresentation = {
      "DOC-MSFT-FY25Q3": { documentType: "监管披露", sourceName: "SEC EDGAR", entity: "Microsoft", reuseCount: 3 },
      "DOC-NVDA-FY26Q1": { documentType: "监管披露", sourceName: "SEC EDGAR", entity: "NVIDIA", reuseCount: 2 },
      "DOC-MSFT-FY25Q3-CALL": { documentType: "业绩说明会", sourceName: "Microsoft IR", entity: "Microsoft", reuseCount: 2 },
      "DOC-TSMC-2025M05": { documentType: "月度经营数据", sourceName: "TSMC IR", entity: "TSMC", reuseCount: 1 },
      "DOC-BRCM-FY25Q2": { documentType: "业绩公告", sourceName: "Broadcom IR", entity: "Broadcom", reuseCount: 0 },
    };
    const params = new URLSearchParams(window.location.search);
    const requestedDocument = params.get("document");
    const selectedDocument = fixture.documents.find((item) => item.id === requestedDocument)
      ?? fixture.documents.find((item) => item.id === "DOC-MSFT-FY25Q3-CALL")
      ?? fixture.documents[0];
    const selectedStatement = fixture.statements.find((item) => item.documentId === selectedDocument.id);
    const selectedLink = fixture.evidenceLinks.find((item) => item.statementId === selectedStatement?.id);
    const selectedThesis = fixture.theses.find((item) => item.id === selectedLink?.thesisId);
    const selectedFactorId = selectedLink?.factorId
      ?? (selectedLink?.id === fixture.case.workbench.mainContradictionEvidenceLinkId ? fixture.case.workbench.mainContradictionFactorId : undefined);
    const selectedFactor = fixture.factors.find((item) => item.id === selectedFactorId)
      ?? fixture.factors.find((item) => item.id === fixture.case.workbench.selectedFactorId);
    const pendingStatement = fixture.statements.find((item) => item.reviewState === "pending_review");
    const pendingLink = fixture.evidenceLinks.find((item) => item.statementId === pendingStatement?.id);
    const selectedPresentation = documentPresentation[selectedDocument.id] ?? {};
    const dateTime = (value) => value ? value.replace("T", " ").replace(/Z$/u, " UTC") : "未记录";
    const reviewRole = {
      support: "支持 · support",
      contradict: "反驳 · contradict",
      background: "背景 · background",
      contextualize: "背景 · background",
      contextualizes: "背景 · background",
      gap: "缺口 · gap",
    };
    const linkedCaseIds = selectedDocument.linkedCaseIds ?? [fixture.case.id];
    const reuseHistory = selectedDocument.reuseHistory ?? linkedCaseIds.map((caseId, index) => ({
      caseId,
      label: caseId === fixture.case.id ? fixture.case.title : "关联研究案例",
      reusedAt: index === 0 ? "2025-06-30 22:40" : "历史快照",
    }));

    return {
      cutoff: fixture.case.cutoff,
      snapshotId: fixture.case.snapshotId,
      documents: fixture.documents
        .map((document) => ({ ...document, ...documentPresentation[document.id] }))
        .sort((left, right) => Number(right.id === selectedDocument.id) - Number(left.id === selectedDocument.id)),
      selected: {
        ...selectedDocument,
        ...selectedPresentation,
        publishedLabel: dateTime(selectedDocument.publishedAt),
        availableLabel: dateTime(selectedDocument.availableAt),
        acquiredLabel: dateTime(selectedDocument.acquiredAt ?? selectedDocument.availableAt),
        previousVersion: selectedDocument.previousVersion ?? "首个归档版本 · 无前序版本",
        linkedCaseIds,
        reuseHistory,
        sourceExcerpt: selectedStatement?.sourceExcerpt ?? selectedStatement?.text ?? "该版本尚无可展示的已定位原文。",
        exactSpan: params.get("span") ?? selectedStatement?.sourceSpan ?? selectedDocument.sourceSpan,
      },
      knowledge: selectedStatement && selectedLink ? {
        statement: selectedStatement,
        link: selectedLink,
        roleLabel: reviewRole[selectedLink.role] ?? selectedLink.role,
        thesis: selectedThesis,
        factor: selectedFactor,
        reviewedBy: selectedLink.reviewedBy ?? "研究审核组",
        reviewedAt: dateTime(selectedLink.reviewedAt ?? "2025-06-30T22:40:00+08:00"),
      } : undefined,
      proposal: pendingStatement ? {
        statement: pendingStatement,
        link: pendingLink,
        roleLabel: reviewRole[pendingLink?.role] ?? "关系待判断",
      } : undefined,
    };
  }

  function renderLibraryFilters() {
    const field = (label, options) => `
      <label class="library-filter">
        <span>${escapeHTML(label)}</span>
        <select aria-label="${escapeHTML(label)}">
          ${options.map((option) => `<option>${escapeHTML(option)}</option>`).join("")}
        </select>
      </label>
    `;
    return `
      <form class="library-filters" data-library-filters aria-label="资料与知识筛选">
        ${field("文档类型", ["全部类型", "监管披露", "业绩说明会"])}
        ${field("来源", ["全部来源", "发行人 IR", "SEC EDGAR"])}
        ${field("发布日期", ["截至 2025-06-30", "2025 年第二季度"])}
        ${field("版本", ["当前冻结版本", "含历史版本"])}
        ${field("审核状态", ["全部状态", "已人工复核", "待人工审核"])}
        ${field("关联案例", ["AI 算力产业链", "全部案例"])}
        ${field("实体", ["全部实体", "Microsoft", "NVIDIA"])}
      </form>
    `;
  }

  function renderLibrarySourceRow(document, selectedId) {
    const isSelected = document.id === selectedId;
    const stateLabel = document.reviewState === "reviewed" ? "已人工复核" : "待人工审核";
    return `
      <a class="library-source-row${isSelected ? " is-selected" : ""}" data-library-source href="?screen=library&amp;document=${encodeURIComponent(document.id)}" ${isSelected ? 'aria-current="true"' : ""}>
        <div class="library-row-top">
          <span class="library-kind">${escapeHTML(document.documentType ?? "来源文档")}</span>
          <span class="library-review-state ${document.reviewState === "reviewed" ? "reviewed" : "pending"}">${escapeHTML(stateLabel)}</span>
        </div>
        <strong data-library-core>${escapeHTML(document.title)}</strong>
        <p data-library-core>${escapeHTML(document.sourceName ?? "已归档来源")} · ${escapeHTML(document.sourceVersion)}</p>
        <div class="library-row-meta"><span>${escapeHTML(document.entity ?? "多实体")}</span><span>复用 ${escapeHTML(document.reuseCount ?? 0)} 次</span></div>
      </a>
    `;
  }

  function renderLibraryWorkbench() {
    const view = buildLibraryViewModel(data);
    const selected = view.selected;
    const knowledge = view.knowledge;
    return `
      <main class="screen library-workbench-screen" data-screen="library">
        <header class="library-heading">
          <div>
            <p class="eyebrow">Research library · point-in-time</p>
            <h1>资料与知识工作台</h1>
            <p class="lede">从不可变来源定位到人工复核知识；待审核 AI 提议始终隔离。</p>
          </div>
          <div class="library-heading-meta">
            <span>证据截止 ${escapeHTML(view.cutoff)}</span>
            <strong>${escapeHTML(view.snapshotId)}</strong>
          </div>
        </header>

        ${renderLibraryFilters()}

        <section class="library-workspace" aria-label="来源与知识工作区">
          <aside class="library-source-layer" data-source-layer aria-labelledby="source-layer-title">
            <header class="library-layer-heading">
              <div><p>IMMUTABLE SOURCE</p><h2 id="source-layer-title">不可变来源层</h2></div>
              <span>DocumentVersion → SourceSpan</span>
            </header>
            <div class="library-source-list">
              ${view.documents.slice(0, 4).map((document) => renderLibrarySourceRow(document, selected.id)).join("")}
            </div>
            <p class="library-ledger-note" data-library-core>原文只新增版本，不覆盖；知识关系引用精确 SourceSpan。</p>
          </aside>

          <div class="library-reading-pane">
            <article class="library-source-inspector" data-selected-source>
              <header class="library-inspector-heading">
                <div>
                  <p class="library-overline">SELECTED DOCUMENTVERSION</p>
                  <h2 data-library-core>${escapeHTML(selected.title)}</h2>
                  <p class="library-version" data-library-core>${escapeHTML(selected.sourceVersion)}</p>
                </div>
                <div class="library-lineage"><span>版本沿革</span><strong>${escapeHTML(selected.previousVersion)}</strong><b aria-hidden="true">→</b><em>当前冻结</em></div>
              </header>
              <dl class="library-time-grid" data-library-core>
                <div><dt>发布时间</dt><dd>${escapeHTML(selected.publishedLabel)}</dd></div>
                <div><dt>首次可用</dt><dd>${escapeHTML(selected.availableLabel)}</dd></div>
                <div><dt>采集时间</dt><dd>${escapeHTML(selected.acquiredLabel)}</dd></div>
                <div><dt>截止日可用</dt><dd>是 · ${escapeHTML(view.cutoff)} 前已可用</dd></div>
              </dl>
              <div class="library-source-detail">
                <div class="library-excerpt">
                  <p class="library-detail-label">精确原文区段 · SourceSpan</p>
                  <code>${escapeHTML(selected.exactSpan)}</code>
                  <blockquote data-library-core>“${escapeHTML(selected.sourceExcerpt)}”</blockquote>
                </div>
                <div class="library-reuse-history">
                  <p class="library-detail-label">关联 ResearchCase · 复用记录</p>
                  <ol>${selected.reuseHistory.map((entry) => `<li><strong data-library-core>${escapeHTML(entry.caseId)}</strong><span data-library-core>${escapeHTML(entry.label)} · ${escapeHTML(entry.reusedAt)}</span></li>`).join("")}</ol>
                </div>
              </div>
            </article>

            <section class="library-knowledge-layer" data-knowledge-layer aria-labelledby="knowledge-layer-title">
              <header class="library-layer-heading">
                <div><p>HUMAN-REVIEWED KNOWLEDGE</p><h2 id="knowledge-layer-title">已复核知识层</h2></div>
                <span>SourceStatement → EvidenceLink</span>
              </header>
              ${knowledge ? `
                <div class="library-knowledge-body">
                  <article class="reviewed-statement">
                    <div class="knowledge-state"><span>已人工复核</span><strong>${escapeHTML(knowledge.roleLabel)}</strong></div>
                    <p class="library-detail-label">规范化 SourceStatement</p>
                    <blockquote data-library-core>${escapeHTML(knowledge.statement.text)}</blockquote>
                    <dl data-library-core>
                      <div><dt>目标 Thesis</dt><dd>${escapeHTML(knowledge.thesis?.title ?? "未关联")}</dd></div>
                      <div><dt>目标因素</dt><dd>${escapeHTML(knowledge.factor?.label ?? "未关联")}</dd></div>
                      <div><dt>复核人</dt><dd>${escapeHTML(knowledge.reviewedBy)}</dd></div>
                      <div><dt>复核时间</dt><dd>${escapeHTML(knowledge.reviewedAt)}</dd></div>
                    </dl>
                  </article>
                  <div class="library-reuse-action">
                    <button type="button" data-library-reuse>引用到研究案例</button>
                    <p data-reuse-note data-library-core>复用现有冻结来源与已复核知识，不复制来源文档。</p>
                    <p class="library-action-status" data-library-status aria-live="polite">尚未选择目标案例</p>
                  </div>
                </div>
              ` : '<p class="library-empty">该来源尚无已复核知识关系。</p>'}
            </section>

            ${view.proposal ? `
              <aside class="library-ai-proposal" data-ai-proposal>
                <div><span>AI 待审核提议</span><strong>未经人工复核</strong></div>
                <p data-library-core>${escapeHTML(view.proposal.statement.text)}</p>
                <small data-library-core>${escapeHTML(view.proposal.roleLabel)} · 隔离队列中，不会进入已复核知识</small>
              </aside>
            ` : ""}
          </div>
        </section>
      </main>
    `;
  }

  function bindLibraryWorkbench(root) {
    const action = root.querySelector("[data-library-reuse]");
    const status = root.querySelector("[data-library-status]");
    if (!action || !status) return;
    action.addEventListener("click", () => {
      status.textContent = "已选择复用 · 下一步选择目标 ResearchCase（原型不写入）";
    });
  }

  function buildDataCenterViewModel(fixture) {
    const workspace = fixture.case.dataCenter;
    const historicalRuns = fixture.providerRuns
      .filter((run) => ["success", "quota_failure", "permission_gap"].includes(run.outcome))
      .slice(0, 4)
      .map((run) => ({
        ...run,
        providerLabel: displayLabel(PRESENTATION.providerNames, run.provider, run.provider),
        outcomeLabel: run.outcome === "success"
          ? "成功"
          : displayLabel(PRESENTATION.providerOutcomes, run.outcome, "失败"),
        detailLabel: displayLabel(PRESENTATION.providerDetails, run.detail, run.detail),
      }));
    return { ...workspace, cutoff: fixture.case.cutoff, snapshotId: fixture.case.snapshotId, historicalRuns };
  }

  function renderDataCenter() {
    const view = buildDataCenterViewModel(data);
    const metric = view.selectedMetric;
    const detailFields = [
      ["指标名称", metric.name],
      ["实体", metric.entity],
      ["数值 / 单位", `${metric.value} ${metric.unit}`],
      ["报告期 / as-of", `${metric.period} · ${metric.asOf}`],
      ["published_at", metric.publishedAt],
      ["available_at", metric.availableAt],
      ["acquired_at", metric.acquiredAt],
      ["来源", metric.source],
      ["方法说明", metric.methodology],
      ["修订", metric.revision],
      ["Provider 运行", metric.providerRunId],
      ["失败含义", metric.failureMeaning],
    ];
    const points = view.series.map((item, index) => {
      const x = 48 + (index * 124);
      const y = 164 - ((item.numericValue - 250) * .72);
      return { ...item, x, y };
    });
    const path = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
    const runTone = { success: "success", quota_failure: "quota", permission_gap: "permission" };

    return `
      <main class="screen data-center-screen" data-screen="data">
        <header class="data-heading">
          <div>
            <p class="eyebrow">Research data · point-in-time</p>
            <h1>点时数据工作台</h1>
            <p class="lede">按冻结快照判断当时可用的数据；今天看到的修订不会回写历史研究案例。</p>
          </div>
          <div class="data-heading-actions">
            <div class="data-snapshot"><span>案例截止 ${escapeHTML(view.cutoff)}</span><strong>${escapeHTML(view.snapshotId)}</strong></div>
            <button type="button" class="data-secondary-action" data-data-runs-action>查看 Provider 运行记录</button>
            <button type="button" class="data-primary-action" data-data-attach>附加冻结数据序列到研究案例</button>
          </div>
        </header>

        <section class="data-workspace" aria-label="指标目录与点时详情">
          <aside class="metric-catalog" data-metric-catalog>
            <header><div><p>METRIC CATALOG</p><h2>指标目录</h2></div><span>${view.catalog.length} 项</span></header>
            <div class="metric-catalog-list">
              ${view.catalog.map((item) => `
                <article class="metric-row${item.id === view.selectedMetricId ? " is-selected" : ""}" data-metric-row${item.id === view.selectedMetricId ? ' aria-current="true"' : ""}>
                  <div><strong>${escapeHTML(item.label)}</strong><span>${escapeHTML(item.id)}</span></div>
                  <p>${escapeHTML(item.entity)} · ${escapeHTML(item.cadence)}</p>
                  <small>${escapeHTML(item.state)}</small>
                </article>
              `).join("")}
            </div>
            <p class="catalog-boundary">目录展示的是研究数据资产，不是交易行情或推荐信号。</p>
          </aside>

          <article class="metric-detail" data-selected-metric>
            <header class="metric-detail-heading">
              <div><p>SELECTED METRIC · ${escapeHTML(view.selectedMetricId)}</p><h2>${escapeHTML(metric.name)}</h2></div>
              <div class="availability-key"><span class="usable">● 截止日可用</span><span class="later">◇ 案例截止日不可用 · 现在已可用</span></div>
            </header>
            <dl class="metric-metadata">
              ${detailFields.map(([label, value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value)}</dd></div>`).join("")}
            </dl>

            <div class="metric-analysis-row">
              <section class="point-in-time-series" aria-labelledby="series-title">
                <header><div><p>POINT-IN-TIME SERIES</p><h3 id="series-title">冻结序列 · 亿美元</h3></div><span>可用性按 acquired_at 判断</span></header>
                <svg viewBox="0 0 590 188" role="img" aria-label="NVIDIA 数据中心业务收入点时序列">
                  <line x1="40" y1="164" x2="558" y2="164"></line>
                  <line x1="40" y1="42" x2="40" y2="164"></line>
                  <path d="${escapeHTML(path)}"></path>
                  ${points.map((point) => `
                    <g class="series-point ${point.cutoffUsable ? "usable" : "later"}" data-series-point data-cutoff-usable="${point.cutoffUsable}">
                      <circle cx="${point.x}" cy="${point.y}" r="6"></circle>
                      <text class="series-value" x="${point.x}" y="${point.y - 13}" text-anchor="middle">${escapeHTML(point.value)}</text>
                      <text x="${point.x}" y="182" text-anchor="middle">${escapeHTML(point.period)}</text>
                    </g>
                  `).join("")}
                </svg>
                <div class="series-availability">
                  ${view.series.map((item) => `<span class="${item.cutoffUsable ? "usable" : "later"}" data-series-point data-cutoff-usable="${item.cutoffUsable}"><b>${escapeHTML(item.period)}</b><small>采集 ${escapeHTML(item.acquiredAt)}</small>${escapeHTML(item.status)}</span>`).join("")}
                </div>
              </section>

              <section class="revision-comparison" data-revision-comparison aria-labelledby="revision-title">
                <header><p>REVISION AUDIT</p><h3 id="revision-title">修订对照</h3></header>
                <div class="revision-columns">
                  <div><span>旧值 · 当时冻结</span><strong>${escapeHTML(view.revisionComparison.oldValue)}</strong><p>${escapeHTML(view.revisionComparison.oldSource)}</p><small>${escapeHTML(view.revisionComparison.oldCutoffMeaning)}</small></div>
                  <b aria-hidden="true">→</b>
                  <div><span>新值 · 后续确认</span><strong>${escapeHTML(view.revisionComparison.newValue)}</strong><p>${escapeHTML(view.revisionComparison.newSource)}</p><small>${escapeHTML(view.revisionComparison.newCutoffMeaning)}</small></div>
                </div>
                <p class="revision-meaning"><strong>为何重要</strong>${escapeHTML(view.revisionComparison.whyItMatters)}</p>
              </section>
            </div>
          </article>
        </section>

        <section class="provider-run-log" data-provider-run-log id="provider-run-log" aria-labelledby="provider-run-title">
          <header>
            <div><p>PROVIDER RUN LEDGER</p><h2 id="provider-run-title">Provider 运行记录</h2></div>
            <p class="provider-meaning-key"><span>成功＝保留来源版本</span><span>失败＝本次没有新数据</span><span>权限＝凭证不可读</span><span>配额＝调用额度耗尽</span></p>
          </header>
          <div class="provider-run-grid">
            ${view.historicalRuns.map((run) => `
              <article class="provider-run ${runTone[run.outcome]}" data-historical-run>
                <div><span>${escapeHTML(run.id)} · 历史运行</span><strong>${escapeHTML(run.providerLabel)}</strong></div>
                <b>${escapeHTML(run.outcomeLabel)}</b>
                <time>${escapeHTML(run.observedAt.replace("T", " "))}</time>
                <p>${escapeHTML(run.detailLabel)}</p>
              </article>
            `).join("")}
            <article class="provider-run planned" data-planned-attempt>
              <div><span>${escapeHTML(view.plannedAttempt.id)} · 新尝试</span><strong>${escapeHTML(view.plannedAttempt.label)}</strong></div>
              <b>${escapeHTML(view.plannedAttempt.state)}</b>
              <time>未产生运行时间</time>
              <p>${escapeHTML(view.plannedAttempt.meaning)}</p>
            </article>
          </div>
        </section>
        <p class="data-action-status" data-data-action-status aria-live="polite">只读原型 · 尚未执行任何操作</p>
      </main>
    `;
  }

  function bindDataCenter(root) {
    const status = root.querySelector("[data-data-action-status]");
    root.querySelector("[data-data-attach]")?.addEventListener("click", () => {
      status.textContent = "已在本地选择冻结序列；原型不写入 ResearchCase。";
    });
    root.querySelector("[data-data-runs-action]")?.addEventListener("click", () => {
      root.querySelector("[data-provider-run-log]")?.classList.add("is-located");
      status.textContent = "已定位 Provider 运行记录；未发起新请求。";
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

  function buildGraphViewModel(fixture) {
    const document = fixture.documents.find((item) => item.id === "DOC-NVDA-FY26Q1");
    const statement = fixture.statements.find((item) => item.id === "ST-002");
    const support = fixture.evidenceLinks.find((item) => item.id === "EL-002");
    const contradiction = fixture.evidenceLinks.find((item) => item.id === "EL-004");
    const factor = fixture.factors.find((item) => item.id === fixture.case.workbench.selectedFactorId);
    const company = fixture.companies.find((item) => item.id === "CO-NVDA");
    const fund = fixture.funds.find((item) => item.id === "FUND-ETF-AI-INFRA");
    const asOf = fixture.case.cutoff;
    const publishedDate = (value) => value?.slice(0, 10) ?? "不适用";
    const node = (definition) => ({
      scope: "AI 算力产业链 · 当前 ResearchCase",
      asOf,
      disclosureDate: "不适用",
      actions: false,
      ...definition,
    });

    return {
      case: fixture.case,
      nodes: [
        node({
          id: document.id,
          layer: "DocumentVersion",
          title: document.title,
          meta: document.sourceVersion,
          kind: "source-fact",
          kindLabel: "来源事实",
          relation: "冻结文档版本，是后续引用的来源边界。",
          review: "已人工复核",
          sourceSpan: document.sourceSpan,
          disclosureDate: publishedDate(document.publishedAt),
          position: "document",
        }),
        node({
          id: statement.id,
          layer: "SourceStatement",
          title: statement.text,
          meta: `${statement.id} · ${statement.sourceVersion}`,
          kind: "source-fact",
          kindLabel: "来源事实",
          relation: "从冻结版本中抽取的原子陈述。",
          review: "已人工复核",
          sourceSpan: statement.sourceSpan,
          disclosureDate: publishedDate(statement.publishedAt),
          position: "statement",
        }),
        node({
          id: support.id,
          layer: "支持证据",
          title: "交付与分部收入具备同主体披露入口",
          meta: `${support.id} · 支持`,
          kind: "reviewed-relation",
          kindLabel: "已人工复核关系",
          relation: support.rationale,
          review: "已人工复核",
          sourceSpan: support.sourceSpan,
          disclosureDate: publishedDate(support.publishedAt),
          position: "support",
        }),
        node({
          id: contradiction.id,
          layer: "反面证据",
          title: "需求与投入不等同于当期收入确认",
          meta: `${contradiction.id} · 反驳即时传导`,
          kind: "reviewed-relation",
          kindLabel: "已人工复核关系",
          relation: contradiction.rationale,
          review: "已人工复核",
          sourceSpan: contradiction.sourceSpan,
          disclosureDate: publishedDate(contradiction.publishedAt),
          position: "contradiction",
        }),
        node({
          id: factor.id,
          layer: "ReviewedFactor",
          title: factor.label,
          meta: `${factor.id} · 传导因素`,
          kind: "reviewed-factor",
          kindLabel: "人工界定因素",
          relation: "在当前案例中作为订单到交付的传导因素使用。",
          review: "因素角色已人工界定",
          sourceSpan: support.sourceSpan,
          position: "factor",
        }),
        node({
          id: "EL-PROPOSED-CAUSAL",
          layer: "CausalStep",
          title: "系统交付兑现后进入分部收入确认",
          meta: "提议边 · 交付 → 收入",
          kind: "ai-proposed",
          kindLabel: "AI 提议关系 · 未经人工复核",
          relation: "提议语义：实际交付是订单积压进入收入确认的必要中间环节。",
          review: "未经人工复核",
          sourceSpan: statement.sourceSpan,
          scope: "NVIDIA 数据中心业务 · 2026 财年第一季度",
          disclosureDate: publishedDate(statement.publishedAt),
          actions: true,
          position: "causal",
        }),
        node({
          id: company.id,
          layer: "Company",
          title: company.name,
          meta: "算力系统供应商",
          kind: "projection",
          kindLabel: "投影节点",
          relation: "从已披露业务主体投影到公司实体。",
          review: "来源映射已复核",
          sourceSpan: company.sourceSpan,
          disclosureDate: company.disclosureDate,
          position: "company",
        }),
        node({
          id: "STOCK-NVDA",
          layer: "Stock",
          title: "NVDA · NASDAQ",
          meta: "证券表达层",
          kind: "projection",
          kindLabel: "投影节点",
          relation: "公司实体到上市证券标识的投影，不表示推荐。",
          review: "投影映射",
          sourceSpan: company.sourceSpan,
          disclosureDate: company.disclosureDate,
          position: "stock",
        }),
        node({
          id: "HOLDING-FUND-NVDA-2025Q1",
          layer: "HoldingDisclosure",
          title: `披露持仓 ${fund.disclosedWeight}`,
          meta: `as-of ${fund.disclosureDate} · ${fund.sourceVersion}`,
          kind: "projection",
          kindLabel: "投影节点",
          relation: "基金定期报告披露的点时持仓，不代表当前敞口。",
          review: "已人工复核",
          sourceSpan: fund.sourceSpan,
          asOf: fund.disclosureDate,
          disclosureDate: publishedDate(fund.publishedAt),
          position: "holding",
        }),
        node({
          id: fund.id,
          layer: "Fund",
          title: fund.name,
          meta: `披露持仓 · as-of ${fund.disclosureDate}`,
          note: "不构成投资建议",
          kind: "projection",
          kindLabel: "投影节点",
          relation: "由已披露持仓连接的基金表达，仅用于点时穿透。",
          review: "披露记录已复核",
          sourceSpan: fund.sourceSpan,
          asOf: fund.disclosureDate,
          disclosureDate: publishedDate(fund.publishedAt),
          position: "fund",
        }),
      ],
    };
  }

  function renderGraphNode(item) {
    return `
      <button type="button" class="graph-node ${escapeHTML(item.kind)} node-${escapeHTML(item.position)}" data-graph-select data-graph-node-id="${escapeHTML(item.id)}" aria-pressed="${item.actions}">
        <span class="graph-node-layer">${escapeHTML(item.layer)}</span>
        <strong>${escapeHTML(item.title)}</strong>
        <small>${escapeHTML(item.meta)}</small>
        <span class="graph-node-kind">${escapeHTML(item.kindLabel)}</span>
        ${item.note ? `<em>${escapeHTML(item.note)}</em>` : ""}
      </button>
    `;
  }

  function renderGraphInspector(item) {
    return `
      <div class="inspector-heading">
        <div><p>当前选择</p><h2>${escapeHTML(item.layer)}</h2></div>
        <span class="inspector-kind ${escapeHTML(item.kind)}">${escapeHTML(item.kindLabel)}</span>
      </div>
      <strong class="inspector-title">${escapeHTML(item.title)}</strong>
      <dl class="inspector-facts">
        <div><dt>对象标识</dt><dd>${escapeHTML(item.id)}</dd></div>
        <div><dt>原文区段</dt><dd>${escapeHTML(item.sourceSpan)}</dd></div>
        <div><dt>关系语义</dt><dd>${escapeHTML(item.relation)}</dd></div>
        <div><dt>审核状态</dt><dd>${escapeHTML(item.review)}</dd></div>
        <div><dt>适用范围</dt><dd>${escapeHTML(item.scope)}</dd></div>
        <div><dt>as-of</dt><dd>${escapeHTML(item.asOf)}</dd></div>
        <div><dt>披露日期</dt><dd>${escapeHTML(item.disclosureDate)}</dd></div>
      </dl>
      ${item.actions ? `
        <div class="inspector-actions">
          <button type="button" class="graph-action primary" data-graph-action="submit">提交审核</button>
          <button type="button" class="graph-action quiet" data-graph-action="withdraw">撤回提议</button>
        </div>
        <p class="inspector-action-note" data-graph-action-note aria-live="polite">操作仅更新本页原型状态，不写入冻结快照。</p>
      ` : `<p class="inspector-boundary">该对象不是待审核提议，不显示审核操作。</p>`}
    `;
  }

  function renderGraphWorkbench() {
    const view = buildGraphViewModel(data);
    const selected = view.nodes.find((item) => item.actions);
    return `
      <main class="screen graph-workbench-screen" data-screen="graph">
        <header class="graph-header">
          <div>
            <p class="eyebrow">Focused relationship path</p>
            <h1>因素关系路径</h1>
            <p class="lede">沿一条重要路径核对来源、关系审核与披露持仓；图谱是可重建投影，不替代证据台账。</p>
          </div>
          <div class="graph-context" aria-label="图谱上下文">
            <span>探索视图</span>
            <strong>当前冻结快照 ${escapeHTML(view.case.snapshotId)}</strong>
            <small>证据截止 ${escapeHTML(view.case.cutoff)} · 待审核提议可见</small>
          </div>
        </header>

        <div class="graph-workbench">
          <section class="graph-primary" aria-labelledby="graph-canvas-title">
            <div class="graph-toolbar">
              <div><p>主路径</p><h2 id="graph-canvas-title">冻结来源 → 披露持仓</h2></div>
              <div class="graph-legend" aria-label="关系图例">
                <span class="legend-source">实线 · 来源</span>
                <span class="legend-reviewed">实线箭头 · 已复核</span>
                <span class="legend-proposed">虚线箭头 · 未复核</span>
              </div>
            </div>
            <div class="relationship-canvas" data-graph-canvas aria-label="证据到基金连续关系图">
              <span class="swimlane-label lane-evidence">01 · 冻结证据</span>
              <span class="swimlane-label lane-mechanism">02 · 因素与机制</span>
              <span class="swimlane-label lane-expression">03 · 证券与基金披露</span>
              ${view.nodes.map(renderGraphNode).join("")}
              <span class="graph-edge edge-document-statement" data-edge-kind="source"><b>抽取</b></span>
              <span class="graph-edge edge-statement-support" data-edge-kind="support"><b>支持</b></span>
              <span class="graph-edge edge-support-factor" data-edge-kind="reviewed"><b>人工确认</b></span>
              <span class="graph-edge edge-contradiction-horizontal" data-edge-kind="contradict"><b>反驳即时传导</b></span>
              <span class="graph-edge edge-contradiction-vertical" data-edge-kind="contradict" aria-hidden="true"></span>
              <span class="graph-edge edge-factor-causal" data-edge-kind="reviewed"><b>形成机制提议</b></span>
              <span class="graph-edge edge-causal-company" data-edge-kind="projection"><b>影响对象</b></span>
              <span class="graph-edge edge-company-stock reverse" data-edge-kind="projection"><b>证券映射</b></span>
              <span class="graph-edge edge-stock-holding reverse" data-edge-kind="projection"><b>披露记录</b></span>
              <span class="graph-edge edge-holding-fund reverse" data-edge-kind="projection"><b>所属基金</b></span>
            </div>

            <div class="structured-path">
              <div><p>可访问替代</p><h2>结构化路径</h2></div>
              <ol aria-label="结构化关系路径">
                ${view.nodes.filter((item) => item.position !== "contradiction").map((item, index) => `
                  <li><button type="button" data-graph-select data-graph-node-id="${escapeHTML(item.id)}"><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.layer)}</small></button></li>
                `).join("")}
                <li class="path-branch"><button type="button" data-graph-select data-graph-node-id="${escapeHTML(view.nodes.find((item) => item.position === "contradiction").id)}"><span>↳</span><strong>反面证据分支</strong><small>反驳即时传导</small></button></li>
              </ol>
            </div>
          </section>

          <aside class="graph-inspector" data-graph-inspector aria-label="关系检查器">
            ${renderGraphInspector(selected)}
          </aside>
        </div>
      </main>
    `;
  }

  function bindGraphWorkbench(root) {
    const view = buildGraphViewModel(data);
    const nodeIndex = new Map(view.nodes.map((item) => [item.id, item]));
    const inspector = root.querySelector("[data-graph-inspector]");
    const selectNode = (id) => {
      const selected = nodeIndex.get(id);
      if (!selected) return;
      root.querySelectorAll("[data-graph-select]").forEach((control) => {
        control.setAttribute("aria-pressed", String(control.dataset.graphNodeId === id));
      });
      inspector.innerHTML = renderGraphInspector(selected);
    };
    root.addEventListener("click", (event) => {
      const selector = event.target.closest("[data-graph-select]");
      if (selector) {
        selectNode(selector.dataset.graphNodeId);
        return;
      }
      const action = event.target.closest("[data-graph-action]");
      if (!action) return;
      const note = inspector.querySelector("[data-graph-action-note]");
      note.textContent = action.dataset.graphAction === "submit"
        ? "已在本页标记为待提交审核，冻结快照保持不变。"
        : "已在本页标记为撤回，冻结快照保持不变。";
    });
  }

  function renderPlanAsset(asset) {
    const assetLabel = asset.kind === "metric"
      ? [
          displayLabel(PRESENTATION.planMetricNames, asset.metricName, "关键业务指标"),
          displayLabel(PRESENTATION.metricValues, asset.metricValue, asset.metricValue),
          displayLabel(PRESENTATION.metricPeriods, asset.metricPeriod, asset.metricPeriod),
        ].join(" · ")
      : asset.label;
    return `
      <article class="asset-row" data-asset-id="${escapeHTML(asset.id)}" data-asset-label="${escapeHTML(assetLabel)}">
        <div><span class="type-label">${escapeHTML(displayLabel(PRESENTATION.assetKinds, asset.kind, "冻结资产"))}</span><strong data-core-text>${escapeHTML(assetLabel)}</strong><p data-secondary-text>${escapeHTML(asset.sourceVersion)} · ${escapeHTML(asset.sourceSpan)}</p></div>
        <div class="asset-review"><span data-core-text data-review-status>${escapeHTML(displayLabel(PRESENTATION.reviewStatesExtended, asset.reviewState, "状态待确认"))} · ${asset.reviewCount} 次复核</span><span data-core-text data-reuse-status>${asset.selected ? "已纳入复用" : "未纳入复用"}</span></div>
        <button type="button" class="plan-button quiet" data-toggle-asset aria-label="${asset.selected ? "移除" : "复用"}：${escapeHTML(assetLabel)}" aria-pressed="${asset.selected}">${asset.selected ? "移除" : "复用"}</button>
      </article>
    `;
  }

  function renderPlan() {
    const view = planState.buildResearchPlanViewModel(data);
    const selectedCount = view.existingAssets.filter((asset) => asset.selected).length;
    const renderCollectionItems = (items, state, label) => items.map((item) => `
      <li data-collection-state="${escapeHTML(state)}"><span class="plan-state-mark" aria-hidden="true">${escapeHTML(label.slice(0, 1))}</span><div><strong data-core-text>${escapeHTML(label)}</strong><p data-core-text>${escapeHTML(item.label)}</p><small data-secondary-text>截止 ${escapeHTML(item.cutoff)}</small></div></li>
    `).join("");
    const renderGapItems = (items) => items.map((gap) => `<div class="gap-entry" data-gap-id="${escapeHTML(gap.id)}" data-gap-label="${escapeHTML(gap.label)}"><div><strong data-core-text>${escapeHTML(gap.label)}</strong><p data-core-text>${escapeHTML(gap.scope)} · <span data-gap-status>待获取</span></p></div><button type="button" class="plan-button quiet" data-toggle-gap aria-label="暂时无法获得：${escapeHTML(gap.label)}" aria-pressed="false">暂时无法获得</button></div>`).join("");
    return `
      <section class="screen research-plan-screen" data-screen="plan">
        <header class="plan-case-header" data-plan-case-header>
          <div>
            <h1>研究计划与证据获取</h1>
            <p class="lede">同一案例内核对复用、外部获取、审核和缺口；所有操作仅改变本页原型状态。</p>
          </div>
          <div class="plan-draft-state"><span aria-hidden="true">◇</span><strong>计划草案</strong><small>需人工确认</small></div>
          <dl class="plan-case-facts">
            <div><dt>ResearchCase</dt><dd>${escapeHTML(view.case.id)}</dd></div>
            <div><dt>研究期间</dt><dd>${escapeHTML(view.case.researchPeriod)}</dd></div>
            <div><dt>证据边界</dt><dd>截止 ${escapeHTML(view.case.cutoff)}</dd></div>
            <div><dt>计划修订</dt><dd>${escapeHTML(view.case.revision)}</dd></div>
          </dl>
        </header>

        <div class="plan-regions">
          <section class="plan-region assets-region" data-plan-region="assets" aria-labelledby="plan-assets-title">
            <header><div><p class="section-kicker">内部复用</p><h2 id="plan-assets-title">已有资料与数据</h2></div><div class="asset-header-tools"><p><strong data-reuse-count>${selectedCount}</strong> 已选 · <strong data-candidate-count>${view.existingAssets.length - selectedCount}</strong> 候选</p><div class="asset-pagination" aria-label="资产分页"><button type="button" data-asset-page="previous" aria-label="上一页资产" disabled>‹</button><span data-asset-page-status aria-live="polite">第 1 / ${Math.ceil(view.orderedAssets.length / view.assetPageSize)} 页</span><button type="button" data-asset-page="next" aria-label="下一页资产">›</button></div></div></header>
            <div class="asset-list">
              ${view.orderedAssets.slice(0, view.assetPageSize).map(renderPlanAsset).join("")}
            </div>
          </section>

          <section class="plan-region providers-region" data-plan-region="providers" aria-labelledby="plan-providers-title">
            <header><div><p class="section-kicker">外部获取</p><h2 id="plan-providers-title">Provider 查询计划</h2></div><p>能力目录 ≠ 已暴露/已授权</p></header>
            <div class="provider-plan-list">
              ${view.providerQueries.map((query) => `
                <article class="provider-plan-row">
                  <div class="provider-title"><span class="plan-state-mark" aria-hidden="true">探</span><div><strong data-core-text>${escapeHTML(displayLabel(PRESENTATION.providerNames, query.provider, "外部数据适配器"))}适配器</strong><p data-core-text>能力目录：${escapeHTML(displayLabel(PRESENTATION.providerCapabilities, query.capability, "待分类能力"))}</p></div></div>
                  <dl><div><dt data-core-text>查询目的</dt><dd data-core-text>${escapeHTML(query.purpose)}</dd></div><div><dt data-core-text>日期范围</dt><dd data-core-text>${escapeHTML(query.dateScope.start)} 至 ${escapeHTML(query.dateScope.end)} · 截止 ${escapeHTML(query.cutoff)}</dd></div><div><dt data-core-text>拟冻结产物</dt><dd data-core-text>${escapeHTML(query.intendedArtifact)}</dd></div><div><dt data-core-text>计划状态</dt><dd data-core-text>${escapeHTML(displayLabel(PRESENTATION.planStates, query.status, "待规划"))} · ${escapeHTML(displayLabel(PRESENTATION.exposureStates, query.exposureStatus, "能力状态待确认"))}</dd></div></dl>
                  <p class="provider-meaning" data-core-text>失败含义：探测失败只表示本次未取得能力证据，不推断接口、参数或替代值。</p>
                </article>
              `).join("")}
            </div>
          </section>

          <section class="plan-region" data-plan-region="collection" aria-labelledby="plan-collection-title">
            <header><div><p class="section-kicker">获取与冻结</p><h2 id="plan-collection-title">获取与冻结状态</h2></div><p>状态不混用</p></header>
            <ul class="collection-list">
              ${renderCollectionItems(view.collection.reused, "reused_frozen", "已复用并冻结")}
              ${renderCollectionItems(view.collection.awaitingProbe, "awaiting_capability_probe", "等待能力探测")}
              ${renderCollectionItems(view.collection.blocked, "blocked_permission", "权限阻塞")}
              ${view.collection.running.length ? renderCollectionItems(view.collection.running, "running", "正在获取") : '<li class="empty-running" data-empty-running><span aria-hidden="true">○</span><strong data-core-text>当前没有运行中的获取任务</strong></li>'}
            </ul>
          </section>

          <section class="plan-region" data-plan-region="pending" aria-labelledby="plan-pending-title">
            <header><div><p class="section-kicker">人工关口</p><h2 id="plan-pending-title">待审核结果</h2></div><p>${view.pendingResults.length} 项</p></header>
            <div class="compact-result-list">
              ${view.pendingResults.map((item) => `<article><span class="plan-state-mark" aria-hidden="true">审</span><div><strong data-core-text>${escapeHTML(item.targetLabel)}</strong><p data-core-text>${escapeHTML(item.task)}</p><div class="pending-provenance"><small data-secondary-text>来源 ${escapeHTML(item.sourceId)} · ${escapeHTML(item.sourceVersion)}</small><span data-core-text data-review-status>${escapeHTML(item.reviewLabel)}</span></div></div></article>`).join("")}
            </div>
          </section>

          <section class="plan-region" data-plan-region="gaps" aria-labelledby="plan-gaps-title">
            <header><div><p class="section-kicker">待补证</p><h2 id="plan-gaps-title">证据缺口</h2></div><a class="plan-text-link" href="?screen=new-research">调整范围</a></header>
            <div class="gap-list">
              <article><span class="type-label">因素缺口</span><div class="gap-group">${renderGapItems(view.gaps.filter((gap) => gap.type === "factor"))}</div></article>
              <article><span class="type-label">正面检索</span><div class="gap-group">${renderGapItems(view.gaps.filter((gap) => gap.type === "positive"))}</div></article>
              <article><span class="type-label">反面检索</span><div class="gap-group">${renderGapItems(view.gaps.filter((gap) => gap.type === "negative"))}</div></article>
              <article class="metric-summary"><span class="type-label">结果指标</span>${view.resultMetrics.map((metric) => `<div><strong data-core-text>${escapeHTML(displayLabel(PRESENTATION.planMetricNames, metric.name, "关键业务指标"))}</strong><p><span data-core-text>${escapeHTML(displayLabel(PRESENTATION.metricValues, metric.value, metric.value))}</span> · <span data-core-text>${escapeHTML(displayLabel(PRESENTATION.metricPeriods, metric.period, metric.period))}</span></p></div>`).join("")}</article>
            </div>
          </section>

          <section class="plan-region" data-plan-region="failures" aria-labelledby="plan-failures-title">
            <header><div><p class="section-kicker">历史运行结果</p><h2 id="plan-failures-title">失败、额度与权限</h2></div><label class="plan-upload"><span>上传材料</span><input type="file" accept=".pdf,.doc,.docx,.xlsx,.csv,.txt"></label></header>
            <p class="upload-status" data-core-text data-upload-status>尚未选择本地材料；不会自动上传。</p>
            <div class="failure-list">
              ${[...view.failures, ...view.manualUploads].map((run) => `<article data-provider-run="${escapeHTML(run.id)}" data-provider-outcome="${escapeHTML(run.outcome)}"><div><strong data-core-text>${escapeHTML(displayLabel(PRESENTATION.providerNames, run.provider, "外部数据接口"))} · ${escapeHTML(displayLabel(PRESENTATION.providerOutcomes, run.outcome, "运行异常"))}</strong><time data-secondary-text>${escapeHTML(run.observedAt)}</time><p data-core-text>${escapeHTML(displayLabel(PRESENTATION.providerDetails, run.detail, "该次历史运行未形成可复用结果，未推断替代值。"))}</p><small data-core-text data-retry-status>${run.outcome === "manual_upload" ? "材料已进入待审核结果，不需要重试。" : "仅记录历史失败，不代表未来计划或替代方案。"}</small></div>${run.outcome === "manual_upload" ? "" : '<button type="button" class="plan-button" data-retry-run>重试</button>'}</article>`).join("")}
            </div>
          </section>
        </div>
        <p class="sr-only" aria-live="polite" data-plan-live></p>
      </section>
    `;
  }

  function bindResearchPlan(root, view) {
    const live = root.querySelector("[data-plan-live]");
    const announce = (message) => { live.textContent = message; };
    const selectedAssets = new Map(view.orderedAssets.map((asset) => [asset.id, asset.selected]));
    const assetList = root.querySelector(".asset-list");
    const pageCount = Math.ceil(view.orderedAssets.length / view.assetPageSize);
    let assetPage = 0;
    const updateAssetCounts = () => {
      const selectedCount = [...selectedAssets.values()].filter(Boolean).length;
      root.querySelector("[data-reuse-count]").textContent = selectedCount;
      root.querySelector("[data-candidate-count]").textContent = selectedAssets.size - selectedCount;
    };
    const renderAssetPage = () => {
      const start = assetPage * view.assetPageSize;
      assetList.innerHTML = view.orderedAssets.slice(start, start + view.assetPageSize)
        .map((asset) => renderPlanAsset({ ...asset, selected: selectedAssets.get(asset.id) }))
        .join("");
      root.querySelector("[data-asset-page-status]").textContent = `第 ${assetPage + 1} / ${pageCount} 页`;
      root.querySelector('[data-asset-page="previous"]').disabled = assetPage === 0;
      root.querySelector('[data-asset-page="next"]').disabled = assetPage === pageCount - 1;
    };
    root.addEventListener("click", (event) => {
      const pageButton = event.target.closest("[data-asset-page]");
      if (pageButton && !pageButton.disabled) {
        assetPage += pageButton.dataset.assetPage === "next" ? 1 : -1;
        renderAssetPage();
        announce(`资产清单已切换到第 ${assetPage + 1} 页，共 ${pageCount} 页`);
        return;
      }
      const assetButton = event.target.closest("[data-toggle-asset]");
      if (assetButton) {
        const selected = assetButton.getAttribute("aria-pressed") !== "true";
        assetButton.setAttribute("aria-pressed", String(selected));
        assetButton.textContent = selected ? "移除" : "复用";
        const row = assetButton.closest("[data-asset-id]");
        assetButton.setAttribute("aria-label", `${selected ? "移除" : "复用"}：${row.dataset.assetLabel}`);
        selectedAssets.set(row.dataset.assetId, selected);
        row.querySelector("[data-reuse-status]").textContent = selected ? "已纳入复用" : "未纳入复用";
        updateAssetCounts();
        announce(`${row.dataset.assetId}${selected ? "已纳入" : "已移出"}本地复用选择`);
        return;
      }
      const retry = event.target.closest("[data-retry-run]");
      if (retry) {
        retry.closest("[data-provider-run]").querySelector("[data-retry-status]").textContent = "已加入重试队列（原型）";
        retry.disabled = true;
        announce("已在本页原型中加入重试队列；尚未执行外部调用");
        return;
      }
      const gapButton = event.target.closest("[data-toggle-gap]");
      if (gapButton) {
        const unavailable = gapButton.getAttribute("aria-pressed") !== "true";
        gapButton.setAttribute("aria-pressed", String(unavailable));
        gapButton.textContent = unavailable ? "恢复获取" : "暂时无法获得";
        const row = gapButton.closest("[data-gap-id]");
        gapButton.setAttribute("aria-label", `${unavailable ? "恢复获取" : "暂时无法获得"}：${row.dataset.gapLabel}`);
        row.querySelector("[data-gap-status]").textContent = unavailable ? "暂时无法获得" : "待获取";
        announce(`${row.dataset.gapId}${unavailable ? "已标记暂时无法获得" : "已恢复为待获取"}`);
      }
    });
    root.querySelector('input[type="file"]').addEventListener("change", (event) => {
      const filename = event.target.files?.[0]?.name;
      root.querySelector("[data-upload-status]").textContent = filename ? `已选择：${filename}；仅保留在本页，尚未上传。` : "尚未选择本地材料；不会自动上传。";
      announce(filename ? `已在本页选择材料 ${filename}` : "已清除本地材料选择");
    });
  }

  function renderCaseContext(view) {
    const facts = [
      ["核心问题", view.case.question, "question"],
      ["研究对象", view.case.researchObject, "object"],
      ["时间范围", view.case.researchPeriod, "period"],
      ["证据截止", view.case.cutoff, "cutoff"],
      ["当前快照", view.case.snapshotId, "snapshot"],
      ["AI 草案状态", view.case.aiState, "ai-state"],
      ["人工复核状态", view.case.humanReviewState, "review-state"],
    ];
    return `
      <header class="case-context" data-case-context>
        <div class="case-context-title">
          <div>
            <p class="case-context-id">${escapeHTML(view.case.id)} · 案例研究工作台</p>
            <h1>${escapeHTML(view.case.title)}</h1>
          </div>
          <div class="case-mode-switch" role="group" aria-label="研究视图">
            <button type="button" data-case-mode="exploration" aria-pressed="true">探索模式</button>
            <button type="button" data-case-mode="frozen" aria-pressed="false">已冻结版本</button>
          </div>
        </div>
        <dl class="case-context-facts">
          ${facts.map(([label, value, kind]) => `<div data-context-kind="${escapeHTML(kind)}"><dt>${escapeHTML(label)}</dt><dd data-core-text>${escapeHTML(value)}</dd></div>`).join("")}
        </dl>
        <div class="case-context-navigation">
          <nav class="case-tabs" role="tablist" aria-label="ResearchCase 内部导航">
            ${view.tabs.map((tab, index) => `<a role="tab" aria-selected="${index === 0 ? "true" : "false"}" href="#${index === 0 ? "case-dossier" : `case-tab-${index + 1}`}">${escapeHTML(tab)}</a>`).join("")}
          </nav>
          <p data-current-basis aria-live="polite"></p>
        </div>
      </header>
    `;
  }

  function renderDecisionCompass(view) {
    return `
      <section class="decision-compass" data-review-compass aria-labelledby="case-dossier">
        <h2 class="sr-only" id="case-dossier">研究档案</h2>
        <article class="formal-judgment" data-decision-kind="formal" data-review-state="reviewed" data-frozen-eligibility="reviewed">
          <div class="decision-heading"><p>人工审核记录</p><span>已冻结 · ${escapeHTML(view.formalJudgment.snapshotId)}</span></div>
          <h3>当前正式判断</h3>
          <p data-core-text>${escapeHTML(view.formalJudgment.text)}</p>
        </article>
        <article class="ai-proposal" data-decision-kind="ai" data-review-state="pending_review" data-frozen-eligibility="excluded" data-provisional data-evidence-assessment>
          <div class="decision-heading"><p>AI 提议</p><span>不写入正式版本</span></div>
          <h3>AI 判断草案</h3>
          <strong>AI 草案 · 未经人工复核</strong>
          <p data-core-text>${escapeHTML(view.aiDraft)}</p>
        </article>
        <div class="reviewer-orienting-cues">
          <article data-decision-kind="contradiction" data-frozen-eligibility="excluded">
            <p>反面线索</p><h3>主要反证</h3><strong data-core-text>${escapeHTML(view.contradiction.label)}</strong>
          </article>
          <article data-decision-kind="gap" data-frozen-eligibility="excluded">
            <p>待补证</p><h3>最大缺口</h3><strong data-core-text>${escapeHTML(view.gap.label)}</strong><small data-core-text>${escapeHTML(view.gap.explanation)}</small>
          </article>
          <article data-decision-kind="next" data-frozen-eligibility="excluded">
            <p>${escapeHTML(view.nextValidation.thesisId)}</p><h3>下一验证事件</h3><strong data-core-text>${escapeHTML(view.nextValidation.event)}</strong>
          </article>
        </div>
      </section>
    `;
  }

  function renderThesisEvidence(view) {
    const selected = view.thesisRows.find((thesis) => thesis.selected);
    const register = view.thesisRows.filter((thesis) => !thesis.selected);
    return `
      <section class="case-section thesis-evidence" data-thesis-evidence data-frozen-eligibility="excluded" aria-labelledby="thesis-evidence-title">
        <header><div><p>命题台账</p><h2 id="thesis-evidence-title">Thesis 与证据</h2></div><span>范围与证伪条件随快照保存</span></header>
        <div class="thesis-focus">
          <article class="selected-thesis" data-thesis-review-state="${escapeHTML(selected.reviewState)}" data-evidence-review-state="${escapeHTML(selected.evidenceReviewState)}" data-frozen-eligibility="${escapeHTML(selected.frozenEligibility)}">
            <div class="thesis-title"><span>${escapeHTML(selected.id)} · 当前验证焦点</span><strong data-core-text>${escapeHTML(selected.title)}</strong></div>
            <dl>
              <div><dt>支持条件</dt><dd data-core-text>${escapeHTML(selected.supportCondition)}</dd></div>
              <div><dt>证据关系</dt><dd data-core-text>${escapeHTML(selected.evidenceState)}；${escapeHTML(selected.relationLabels)}</dd></div>
              <div><dt>适用范围</dt><dd data-core-text>${escapeHTML(selected.scope)}</dd></div>
              <div><dt>证伪条件</dt><dd data-core-text>${escapeHTML(selected.falsifier)}</dd></div>
            </dl>
          </article>
          <div class="thesis-register" aria-label="其他命题">
            ${register.map((thesis) => `
              <article data-thesis-review-state="${escapeHTML(thesis.reviewState)}" data-evidence-review-state="${escapeHTML(thesis.evidenceReviewState)}" data-frozen-eligibility="${escapeHTML(thesis.frozenEligibility)}">
                <div class="thesis-title"><span>${escapeHTML(thesis.id)}</span><strong data-core-text>${escapeHTML(thesis.title)}</strong></div>
                <small data-core-text>证据关系：${escapeHTML(thesis.evidenceState)} · 适用范围：${escapeHTML(thesis.scope)}</small>
              </article>
            `).join("")}
          </div>
          <article class="thesis-rebuttal" data-thesis-rebuttal data-evidence-role="${escapeHTML(view.rebuttal.relation)}" data-review-state="${escapeHTML(view.rebuttal.reviewState)}" data-snapshot-membership="${escapeHTML(view.rebuttal.snapshotMembership)}" data-frozen-eligibility="${escapeHTML(view.rebuttal.frozenEligibility)}">
            <div class="rebuttal-statement"><span>反驳证据 · ${escapeHTML(view.rebuttal.id)}</span><strong data-core-text>${escapeHTML(view.rebuttal.statement)}</strong></div>
            <dl>
              <div><dt>来源文档</dt><dd data-core-text>${escapeHTML(view.rebuttal.documentTitle)}</dd></div>
              <div><dt>文档版本</dt><dd data-core-text>${escapeHTML(view.rebuttal.sourceVersion)}</dd></div>
              <div><dt>发布日期</dt><dd data-core-text>${escapeHTML(view.rebuttal.publishedDate)}</dd></div>
              <div><dt>原文区段</dt><dd data-core-text>${escapeHTML(view.rebuttal.sourceSpan)}</dd></div>
              <div><dt>复核状态</dt><dd data-core-text>${escapeHTML(view.rebuttal.reviewLabel)}</dd></div>
            </dl>
            <a class="source-locator" href="?screen=library&amp;document=${encodeURIComponent(view.rebuttal.documentId)}&amp;span=${encodeURIComponent(view.rebuttal.sourceSpan)}">查看原文定位</a>
          </article>
        </div>
      </section>
    `;
  }

  function renderFactorComparison(view) {
    const dimensions = [
      ["时间顺序", "timeOrder"],
      ["传导机制", "mechanism"],
      ["直接证据", "directEvidence"],
      ["替代解释", "alternatives"],
      ["差异解释", "differenceExplanation"],
      ["适用边界", "scope"],
      ["反例和证伪条件", "falsifier"],
    ];
    return `
      <section class="case-section factor-comparison" data-factor-comparison data-frozen-eligibility="excluded" data-provisional aria-labelledby="factor-comparison-title">
        <header><div><p>竞争性解释</p><h2 id="factor-comparison-title">因素比较</h2></div><span data-provisional>AI 比较草案 · 未经人工复核</span></header>
        <div class="factor-table-wrap">
          <table>
            <thead><tr><th scope="col">审核维度 · 因素角色与状态</th>${view.factorRows.map((factor) => `
              <th scope="col"${factor.factorId === view.selectedFactor.id ? ' class="selected-factor-row"' : ""}>
                <span>${escapeHTML(factor.groupLabel)} · ${escapeHTML(factor.roleLabel)} · ${escapeHTML(factor.statusLabel)}</span><strong>${escapeHTML(factor.label)}</strong>
              </th>
            `).join("")}</tr></thead>
            <tbody>
              ${dimensions.map(([label, field]) => `
                <tr><th scope="row">${escapeHTML(label)}</th>${view.factorRows.map((factor) => `<td data-factor-label="${escapeHTML(factor.label)}" data-core-text>${escapeHTML(factor[field])}</td>`).join("")}</tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </section>
    `;
  }

  function renderFactorDetail(view) {
    const detail = view.selectedFactor;
    const fields = [
      ["机制", detail.mechanism],
      ["直接证据", detail.directEvidence],
      ["反例", detail.counterexample],
      ["替代解释", detail.alternatives],
      ["影响对象", detail.impactObject],
      ["适用范围", detail.scope],
      ["证伪条件", detail.falsifier],
    ];
    return `
      <section class="case-section factor-detail" data-factor-detail data-frozen-eligibility="excluded" data-provisional aria-labelledby="factor-detail-title">
        <header><div><p>${escapeHTML(detail.roleLabel)} · ${escapeHTML(detail.statusLabel)}</p><h2 id="factor-detail-title">所选因素解释</h2></div><strong>${escapeHTML(detail.label)}</strong></header>
        <dl>${fields.map(([label, value]) => `<div><dt>${escapeHTML(label)}</dt><dd data-core-text>${escapeHTML(value)}</dd></div>`).join("")}</dl>
      </section>
    `;
  }

  function renderSources(view) {
    return `
      <section class="case-section source-citations" aria-labelledby="source-citations-title">
        <header><div><p>冻结引用清单</p><h2 id="source-citations-title">原文引用</h2></div><span>每条关系回到确切 SourceSpan</span></header>
        <div class="source-list" data-source-list>
          ${view.sources.map((source) => `
            <article data-source-citation data-evidence-role="${escapeHTML(source.relation)}" data-review-state="${escapeHTML(source.reviewState)}" data-snapshot-membership="${escapeHTML(source.snapshotMembership)}" data-frozen-eligibility="${escapeHTML(source.frozenEligibility)}"${source.frozenEligibility === "excluded" ? " data-provisional" : ""}>
              <div class="source-statement"><span>${escapeHTML(source.relationLabel)} · ${escapeHTML(source.id)}</span><strong data-core-text>${escapeHTML(source.statement)}</strong></div>
              <dl>
                <div><dt>文档版本</dt><dd data-core-text>${escapeHTML(source.sourceVersion)}</dd></div>
                <div><dt>发布日期</dt><dd data-core-text><time datetime="${escapeHTML(source.publishedDate)}">${escapeHTML(source.publishedDate)}</time></dd></div>
                <div><dt>原文区段</dt><dd data-core-text>${escapeHTML(source.sourceSpan)}</dd></div>
                <div><dt>复核状态</dt><dd data-core-text>${escapeHTML(source.reviewLabel)}</dd></div>
                <div><dt>快照归属</dt><dd data-core-text>${escapeHTML(source.snapshotMembership)}</dd></div>
              </dl>
              <a class="source-locator" href="?screen=library&amp;document=${encodeURIComponent(source.documentId)}&amp;span=${encodeURIComponent(source.sourceSpan)}">查看原文定位</a>
            </article>
          `).join("")}
        </div>
      </section>
    `;
  }

  function renderCaseWorkbench() {
    const view = caseState.buildCaseWorkbenchViewModel(data);
    return `
      <main class="screen case-workbench-screen" data-screen="case">
        ${renderCaseContext(view)}
        <div class="case-reading" data-case-reading>
          ${renderDecisionCompass(view)}
          ${renderThesisEvidence(view)}
          ${renderFactorComparison(view)}
          <div class="case-bottom-grid">
            ${renderFactorDetail(view)}
            ${renderSources(view)}
          </div>
        </div>
      </main>
    `;
  }

  function renderVersionItems(items, formatter) {
    return items.map((item) => `<li data-core-text>${formatter(item)}</li>`).join("");
  }

  function renderVersionSnapshotColumn(side, snapshot, content) {
    const isBefore = side === "before";
    return `
      <section class="version-snapshot-column ${escapeHTML(side)}" data-snapshot-column="${escapeHTML(side)}" data-comparison-step="${escapeHTML(side)}">
        <header class="version-column-heading">
          <span>${isBefore ? "变更前" : "变更后"}</span>
          <strong>${escapeHTML(snapshot.id)}</strong>
        </header>
        <article class="formal-conclusion">
          <div><span>正式结论</span>${badge(content.formalConclusion.state, isBefore ? "warning" : "reviewed")}</div>
          <p data-core-text>${escapeHTML(content.formalConclusion.text)}</p>
        </article>
        <section class="version-change-group">
          <h3>DocumentVersion / 数据序列</h3>
          <ul class="version-record-list inputs">
            ${renderVersionItems(content.inputs, (item) => `<strong>${escapeHTML(item.id)}</strong><span>${escapeHTML(item.label)}</span><code>${escapeHTML(item.version)}</code>`)}
          </ul>
        </section>
        <section class="version-change-group">
          <h3>已审核关系 ${isBefore ? "" : '<span class="new-contradiction">新反面证据</span>'}</h3>
          <ul class="version-record-list compact">
            ${renderVersionItems(content.relationships, (item) => `<strong>${escapeHTML(item.id)}</strong><span>${escapeHTML(item.label)}</span><em>${escapeHTML(item.role)} · ${escapeHTML(item.reviewState)}</em>`)}
          </ul>
        </section>
        <section class="version-change-group">
          <h3>因素角色</h3>
          <ul class="version-record-list compact factor-records">
            ${renderVersionItems(content.factors, (item) => `<strong>${escapeHTML(item.id)}</strong><span>${escapeHTML(item.label)}</span><em>${escapeHTML(item.role)}</em>`)}
          </ul>
        </section>
        <section class="version-change-group gap-group">
          <h3>证据缺口</h3>
          <ul class="version-record-list compact">
            ${renderVersionItems(content.gaps, (item) => `<strong>${escapeHTML(item.state)}</strong><span>${escapeHTML(item.label)}</span>`)}
          </ul>
        </section>
      </section>
    `;
  }

  function renderChangeRail(change) {
    const changes = [
      ["输入变化", change.inputSummary],
      ["关系变化", change.relationshipSummary],
      ["角色变化", change.factorSummary],
      ["正式结论变化", change.conclusionSummary],
      ["缺口状态", change.gapSummary],
    ];
    return `
      <aside class="version-change-rail" data-change-rail data-comparison-step="change">
        <header><span aria-hidden="true">→</span><strong>为什么改变</strong></header>
        <ol>
          ${changes.map(([label, text]) => `<li><span>${escapeHTML(label)}</span><p data-core-text>${escapeHTML(text)}</p></li>`).join("")}
        </ol>
        <section class="human-rationale" data-human-rationale>
          <h3>人工变更理由</h3>
          <p data-core-text>${escapeHTML(change.rationale)}</p>
          <small>${escapeHTML(change.reviewedBy)} · ${escapeHTML(change.reviewedAt)}</small>
        </section>
      </aside>
    `;
  }

  function renderVersionsWorkbench() {
    const view = versionsState.buildVersionsViewModel(data);
    return `
      <main class="screen versions-screen" data-screen="versions">
        <header class="versions-heading">
          <div>
            <p class="eyebrow">ResearchCase · point-in-time</p>
            <h1>冻结快照比较</h1>
            <p class="lede"><strong>${escapeHTML(view.case.id)}</strong> · ${escapeHTML(view.case.title)}</p>
          </div>
          <div class="versions-actions">
            <a href="?screen=library&source=version-change">查看变更来源</a>
            <a href="?screen=review&item=EL-004">查看审核记录</a>
          </div>
        </header>

        <section class="snapshot-selection" aria-label="已选择的冻结快照">
          <button type="button" aria-pressed="true" data-snapshot-selector data-snapshot-id="${escapeHTML(view.beforeSnapshot.id)}">
            <span>基准快照</span><strong>${escapeHTML(view.beforeSnapshot.id)}</strong>
            <small>截止 ${escapeHTML(view.beforeSnapshot.cutoff)} · 冻结 ${escapeHTML(view.beforeSnapshot.freezeTime)}</small>
          </button>
          <span class="selection-arrow" aria-hidden="true">→</span>
          <button type="button" aria-pressed="true" data-snapshot-selector data-snapshot-id="${escapeHTML(view.afterSnapshot.id)}">
            <span>对比快照</span><strong>${escapeHTML(view.afterSnapshot.id)}</strong>
            <small>截止 ${escapeHTML(view.afterSnapshot.cutoff)} · 冻结 ${escapeHTML(view.afterSnapshot.freezeTime)}</small>
          </button>
          <p>同一案例 · 同一研究对象 · 仅比较各自截止时点已可用且已冻结的正式内容</p>
        </section>

        <section class="versions-comparison" data-formal-snapshot-comparison aria-label="正式冻结快照语义比较">
          ${renderVersionSnapshotColumn("before", view.beforeSnapshot, view.before)}
          ${renderChangeRail(view.changeRail)}
          ${renderVersionSnapshotColumn("after", view.afterSnapshot, view.after)}
        </section>

        <aside class="ai-version-proposal" data-ai-proposal>
          <div class="ai-proposal-label"><span>AI rerun</span><strong>${escapeHTML(view.aiProposal.label)}</strong></div>
          <div><strong>${escapeHTML(view.aiProposal.runId)} · ${escapeHTML(view.aiProposal.observedAt)}</strong><p data-core-text>${escapeHTML(view.aiProposal.text)}</p></div>
          <p data-core-text>${escapeHTML(view.aiProposal.boundary)}</p>
        </aside>
      </main>
    `;
  }

  const SCREEN_RENDERERS = {
    "overview": renderOverview,
    "new-research": renderNewResearch,
    "plan": renderPlan,
    "case": renderCaseWorkbench,
    "graph": renderGraphWorkbench,
    "review": renderReviewWorkbench,
    "library": renderLibraryWorkbench,
    "data": renderDataCenter,
    "versions": renderVersionsWorkbench,
  };

  function requestedScreen() {
    const candidate = new URLSearchParams(window.location.search).get("screen") ?? "overview";
    return Object.hasOwn(SCREEN_RENDERERS, candidate) ? candidate : "overview";
  }

  function renderShell(screen) {
    teardownNewResearchAutosize();
    teardownNewResearchAutosize = () => {};
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
    if (screen === "plan") bindResearchPlan(app.querySelector("[data-screen=plan]"), planState.buildResearchPlanViewModel(data));
    if (screen === "case") caseState.bindCaseWorkbench(app.querySelector("[data-screen=case]"));
    if (screen === "graph") bindGraphWorkbench(app.querySelector("[data-screen=graph]"));
    if (screen === "review") bindReviewWorkbench(app.querySelector("[data-screen=review]"));
    if (screen === "library") bindLibraryWorkbench(app.querySelector("[data-screen=library]"));
    if (screen === "data") bindDataCenter(app.querySelector("[data-screen=data]"));
  }

  renderShell(requestedScreen());
  window.addEventListener("pageshow", () => autoSizeThesisTextareas(app));
  window.SCREEN_RENDERERS = SCREEN_RENDERERS;
  window.PROTOTYPE_OVERVIEW = Object.freeze({ buildOverviewViewModel });
  window.PROTOTYPE_NEW_RESEARCH = Object.freeze({ buildNewResearchViewModel, confirmationStorageKey });
  window.PROTOTYPE_RESEARCH_PLAN = planState;
  window.PROTOTYPE_CASE_WORKBENCH = caseState;
  window.PROTOTYPE_GRAPH_WORKBENCH = Object.freeze({ buildGraphViewModel });
  window.PROTOTYPE_LIBRARY_WORKBENCH = Object.freeze({ buildLibraryViewModel });
  window.PROTOTYPE_DATA_CENTER = Object.freeze({ buildDataCenterViewModel });
  window.PROTOTYPE_VERSIONS = versionsState;
}());
