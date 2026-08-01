(function () {
  "use strict";

  const CASE_TABS = Object.freeze([
    "研究档案",
    "命题与证据",
    "因素分析",
    "关系路径",
    "公司与基金",
    "历史版本",
  ]);

  const REVIEW_LABELS = Object.freeze({
    reviewed: "已人工复核",
    pending_review: "待人工审核",
  });

  const EVIDENCE_ROLE_LABELS = Object.freeze({
    support: "支持",
    gap: "缺口",
    contradict: "反驳",
  });

  const FACTOR_ROLE_LABELS = Object.freeze({
    candidate: "候选因素",
    transmission_factor: "传导因素",
    constraint: "限制因素",
    background: "背景因素",
  });

  const FACTOR_STATUS_LABELS = Object.freeze({
    candidate: "候选因素",
  });

  const FACTOR_GROUP_LABELS = Object.freeze({
    demand: "需求起点",
    supply: "供给条件",
    transmission: "传导因素",
    constraints: "限制因素",
    alternatives: "替代解释",
    contradiction: "矛盾观察",
  });

  const THESIS_EVIDENCE_LABELS = Object.freeze({
    reviewed_links_present: "已有已审核关系",
    pending_relationship_review: "已有已审核关系 · 另有待审核关系",
    no_evidence_links: "尚无证据关系",
  });

  function indexById(records) {
    return new Map(records.map((record) => [record.id, record]));
  }

  function buildCaseWorkbenchViewModel(fixture) {
    const dossier = fixture.case.workbench;
    const factors = indexById(fixture.factors);
    const theses = indexById(fixture.theses);
    const evidenceLinks = indexById(fixture.evidenceLinks);
    const statements = indexById(fixture.statements);
    const documents = indexById(fixture.documents);
    const currentSnapshot = fixture.snapshots.find((snapshot) => snapshot.id === fixture.case.snapshotId);
    const analyses = new Map(dossier.factorAnalyses.map((analysis) => [analysis.factorId, analysis]));
    const contradiction = factors.get(dossier.mainContradictionFactorId);
    const gap = factors.get(dossier.largestGapFactorId);
    const nextValidationThesis = theses.get(dossier.nextValidationThesisId);
    const selectedAnalysis = analyses.get(dossier.selectedFactorId);
    const selectedFactor = factors.get(dossier.selectedFactorId);

    const resolveSource = (link) => {
      const statement = statements.get(link.statementId);
      const document = documents.get(statement.documentId);
      return {
        id: link.id,
        documentId: document.id,
        documentTitle: document.title,
        statement: statement.text,
        rationale: link.rationale,
        relation: link.role,
        relationLabel: EVIDENCE_ROLE_LABELS[link.role] ?? "关系",
        sourceVersion: link.sourceVersion,
        publishedDate: link.publishedAt.slice(0, 10),
        availableDate: link.availableAt.slice(0, 10),
        sourceSpan: link.sourceSpan,
        reviewState: link.reviewState,
        reviewLabel: REVIEW_LABELS[link.reviewState] ?? "状态待确认",
        snapshotMembership: link.snapshotMembership.join("、"),
        frozenEligibility: link.reviewState === "reviewed" ? "reviewed" : "excluded",
      };
    };

    const thesisRows = fixture.theses.map((thesis) => {
      const links = fixture.evidenceLinks.filter((link) => link.thesisId === thesis.id);
      const relationLabels = links.length
        ? links.map((link) => `${EVIDENCE_ROLE_LABELS[link.role] ?? "关系"} · ${REVIEW_LABELS[link.reviewState] ?? "状态待确认"}`).join("；")
        : "尚无证据关系";
      return {
        id: thesis.id,
        selected: thesis.id === dossier.nextValidationThesisId,
        reviewState: "unreviewed",
        frozenEligibility: "excluded",
        title: thesis.title,
        statement: thesis.statement,
        supportCondition: thesis.supportCondition,
        evidenceReviewState: thesis.evidenceReviewState,
        evidenceState: THESIS_EVIDENCE_LABELS[thesis.evidenceReviewState] ?? "证据关系状态待确认",
        relationLabels,
        scope: `${thesis.observationStart} 至 ${thesis.observationEnd}`,
        falsifier: thesis.falsifier,
      };
    });

    const factorRows = dossier.factorAnalyses.map((analysis) => {
      const factor = factors.get(analysis.factorId);
      return {
        ...analysis,
        label: factor.label,
        groupLabel: FACTOR_GROUP_LABELS[factor.group] ?? "因素",
        roleLabel: FACTOR_ROLE_LABELS[analysis.proposedRole] ?? "角色待审核",
        statusLabel: FACTOR_STATUS_LABELS[factor.status] ?? "状态待审核",
      };
    });

    const sources = dossier.sourceEvidenceLinkIds.map((linkId) => resolveSource(evidenceLinks.get(linkId)));
    const rebuttal = resolveSource(evidenceLinks.get(dossier.mainContradictionEvidenceLinkId));

    return {
      tabs: CASE_TABS,
      case: {
        id: fixture.case.id,
        title: fixture.case.title,
        question: fixture.case.question,
        researchObject: fixture.case.researchObject,
        researchPeriod: `${fixture.case.researchPeriod.start} 至 ${fixture.case.researchPeriod.end}`,
        cutoff: fixture.case.cutoff,
        snapshotId: fixture.case.snapshotId,
        snapshotFrozenAt: currentSnapshot.frozenAt,
        aiState: fixture.case.aiLabel,
        humanReviewState: "人工复核 · 正式判断已冻结",
      },
      formalJudgment: dossier.formalJudgment,
      aiDraft: fixture.case.provisionalAssessment,
      contradiction,
      gap: {
        ...gap,
        explanation: analyses.get(gap.id)?.directEvidence ?? "当前冻结快照尚无可定位的直接证据。",
      },
      nextValidation: {
        thesisId: nextValidationThesis.id,
        event: nextValidationThesis.nextValidationEvent,
      },
      thesisRows,
      rebuttal,
      factorRows,
      selectedFactor: {
        id: selectedFactor.id,
        label: selectedFactor.label,
        roleLabel: FACTOR_ROLE_LABELS[selectedAnalysis.proposedRole] ?? "角色待审核",
        statusLabel: `${FACTOR_STATUS_LABELS[selectedFactor.status] ?? "状态待审核"} · 角色待人工审核`,
        mechanism: selectedAnalysis.mechanism,
        directEvidence: selectedAnalysis.directEvidence,
        counterexample: selectedAnalysis.falsifier,
        alternatives: selectedAnalysis.alternatives,
        impactObject: "订单积压、系统交付与同口径分部收入",
        scope: selectedAnalysis.scope,
        falsifier: selectedAnalysis.falsifier,
      },
      sources,
    };
  }

  function setCaseWorkbenchMode(root, mode) {
    const frozen = mode === "frozen";
    root.dataset.viewMode = frozen ? "frozen" : "exploration";
    for (const button of root.querySelectorAll("[data-case-mode]")) {
      button.setAttribute("aria-pressed", String(button.dataset.caseMode === root.dataset.viewMode));
    }
    root.querySelector("[data-current-basis]").textContent = frozen
      ? "已冻结版本 · 只显示当前快照中的资料、数据和已审核关系"
      : "探索模式 · 同时显示新材料与 AI 提议；未经审核内容不会改变正式判断";
    for (const element of root.querySelectorAll("[data-frozen-eligibility]")) {
      const excluded = frozen && element.dataset.frozenEligibility !== "reviewed";
      element.hidden = excluded;
      element.inert = excluded;
      if (excluded) element.setAttribute("aria-hidden", "true");
      else element.removeAttribute("aria-hidden");
    }
  }

  function bindCaseWorkbench(root) {
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-case-mode]");
      if (button) setCaseWorkbenchMode(root, button.dataset.caseMode);
    });
    setCaseWorkbenchMode(root, "exploration");
  }

  window.CASE_WORKBENCH_STATE = Object.freeze({
    CASE_TABS,
    buildCaseWorkbenchViewModel,
    bindCaseWorkbench,
    setCaseWorkbenchMode,
  });
}());
