(function () {
  "use strict";

  const data = window.PROTOTYPE_DATA;
  const researchState = window.NEW_RESEARCH_STATE;
  const planState = window.RESEARCH_PLAN_STATE;
  const caseState = window.CASE_WORKBENCH_STATE;
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
        <article class="formal-judgment" data-decision-kind="formal" data-review-state="reviewed">
          <div class="decision-heading"><p>人工审核记录</p><span>已冻结 · ${escapeHTML(view.formalJudgment.snapshotId)}</span></div>
          <h3>当前正式判断</h3>
          <p data-core-text>${escapeHTML(view.formalJudgment.text)}</p>
        </article>
        <article class="ai-proposal" data-decision-kind="ai" data-review-state="pending_review" data-provisional data-evidence-assessment>
          <div class="decision-heading"><p>AI 提议</p><span>不写入正式版本</span></div>
          <h3>AI 判断草案</h3>
          <strong>AI 草案 · 未经人工复核</strong>
          <p data-core-text>${escapeHTML(view.aiDraft)}</p>
        </article>
        <div class="reviewer-orienting-cues">
          <article data-decision-kind="contradiction">
            <p>反面线索</p><h3>主要反证</h3><strong data-core-text>${escapeHTML(view.contradiction.label)}</strong>
          </article>
          <article data-decision-kind="gap">
            <p>待补证</p><h3>最大缺口</h3><strong data-core-text>${escapeHTML(view.gap.label)}</strong><small data-core-text>${escapeHTML(view.gap.explanation)}</small>
          </article>
          <article data-decision-kind="next">
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
      <section class="case-section thesis-evidence" data-thesis-evidence aria-labelledby="thesis-evidence-title">
        <header><div><p>命题台账</p><h2 id="thesis-evidence-title">Thesis 与证据</h2></div><span>范围与证伪条件随快照保存</span></header>
        <div class="thesis-focus">
          <article class="selected-thesis">
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
              <article>
                <div class="thesis-title"><span>${escapeHTML(thesis.id)}</span><strong data-core-text>${escapeHTML(thesis.title)}</strong></div>
                <small data-core-text>证据关系：${escapeHTML(thesis.evidenceState)} · 适用范围：${escapeHTML(thesis.scope)}</small>
              </article>
            `).join("")}
          </div>
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
      <section class="case-section factor-comparison" data-factor-comparison data-provisional aria-labelledby="factor-comparison-title">
        <header><div><p>竞争性解释</p><h2 id="factor-comparison-title">因素比较</h2></div><span data-provisional>AI 比较草案 · 未经人工复核</span></header>
        <div class="factor-table-wrap">
          <table>
            <thead><tr><th scope="col">审核维度 · 因素角色与状态</th>${view.factorRows.map((factor) => `
              <th scope="col"${factor.factorId === view.selectedFactor.id ? ' class="selected-factor-row"' : ""}>
                <span>${escapeHTML(factor.groupLabel)}</span><strong>${escapeHTML(factor.label)}</strong><small>${escapeHTML(factor.roleLabel)} · ${escapeHTML(factor.statusLabel)}</small>
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
      <section class="case-section factor-detail" data-factor-detail data-provisional aria-labelledby="factor-detail-title">
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
            <article data-source-citation${source.provisional ? " data-provisional" : ""}>
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

  const SCREEN_RENDERERS = {
    "overview": renderOverview,
    "new-research": renderNewResearch,
    "plan": renderPlan,
    "case": renderCaseWorkbench,
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
  }

  renderShell(requestedScreen());
  window.addEventListener("pageshow", () => autoSizeThesisTextareas(app));
  window.SCREEN_RENDERERS = SCREEN_RENDERERS;
  window.PROTOTYPE_OVERVIEW = Object.freeze({ buildOverviewViewModel });
  window.PROTOTYPE_NEW_RESEARCH = Object.freeze({ buildNewResearchViewModel, confirmationStorageKey });
  window.PROTOTYPE_RESEARCH_PLAN = planState;
  window.PROTOTYPE_CASE_WORKBENCH = caseState;
}());
