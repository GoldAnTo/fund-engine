(function () {
  "use strict";

  function indexById(records) {
    return new Map(records.map((record) => [record.id, record]));
  }

  function reusableIds(plan) {
    return new Set([
      ...plan.reusableAssets.documentIds,
      ...plan.reusableAssets.statementIds,
      ...plan.reusableAssets.metricIds,
      ...plan.reusableAssets.evidenceLinkIds,
    ]);
  }

  function buildExistingAssets(fixture) {
    const selected = reusableIds(fixture.case.researchPlan);
    const currentSnapshot = fixture.case.snapshotId;
    const rows = [
      ...fixture.documents.map((item) => ({ ...item, kind: "document", label: item.title })),
      ...fixture.statements.map((item) => ({ ...item, kind: "statement", label: item.text })),
      ...fixture.metrics.map((item) => ({ ...item, kind: "metric", label: `${item.name} · ${item.value} · ${item.period}` })),
      ...fixture.evidenceLinks
        .filter((item) => item.reviewState === "reviewed")
        .map((item) => ({ ...item, kind: "evidence_link", label: `${item.statementId} → ${item.thesisId ?? item.factorId}` })),
    ];
    return rows
      .filter((item) => item.snapshotMembership?.includes(currentSnapshot))
      .map((item) => ({
        id: item.id,
        kind: item.kind,
        label: item.label,
        sourceVersion: item.sourceVersion,
        sourceSpan: item.sourceSpan,
        reviewState: item.reviewState,
        reviewCount: item.reviewState === "reviewed" ? 1 : 0,
        selected: selected.has(item.id),
      }));
  }

  function targetLabel(targetId, fixture) {
    const target = [
      ...fixture.documents,
      ...fixture.statements,
      ...fixture.evidenceLinks,
      ...fixture.metrics,
      ...fixture.companies,
      ...fixture.funds,
      ...fixture.theses,
      ...fixture.factors,
    ].find((item) => item.id === targetId);
    return target?.title ?? target?.text ?? target?.name ?? target?.label ?? targetId;
  }

  function buildPendingResults(fixture) {
    const queue = fixture.reviewQueue.map((item) => ({
      id: item.id,
      sourceId: item.targetId,
      sourceVersion: item.sourceVersion,
      targetLabel: targetLabel(item.targetId, fixture),
      task: item.task,
      reviewState: item.reviewState,
      reviewLabel: "待人工审核",
    }));
    const queuedTargets = new Set(queue.map((item) => item.sourceId));
    const links = fixture.evidenceLinks
      .filter((item) => item.reviewState === "pending_review" && !queuedTargets.has(item.id))
      .map((item) => ({
        id: item.id,
        sourceId: item.statementId,
        sourceVersion: item.sourceVersion,
        targetLabel: targetLabel(item.thesisId ?? item.factorId, fixture),
        task: "审核证据与目标之间的关系",
        reviewState: item.reviewState,
        reviewLabel: "关系待人工审核",
      }));
    return [...queue, ...links];
  }

  function buildResearchPlanViewModel(fixture) {
    const plan = fixture.case.researchPlan;
    const factors = indexById(fixture.factors);
    const failureOutcomes = new Set(["quota_failure", "permission_gap"]);
    const collectionGroups = Object.groupBy
      ? Object.groupBy(plan.collectionTasks, (task) => task.state)
      : plan.collectionTasks.reduce((groups, task) => {
          (groups[task.state] ??= []).push(task);
          return groups;
        }, {});
    return {
      case: {
        id: fixture.case.id,
        title: fixture.case.title,
        researchPeriod: `${fixture.case.researchPeriod.start} 至 ${fixture.case.researchPeriod.end}`,
        cutoff: fixture.case.cutoff,
        revision: plan.revision,
      },
      existingAssets: buildExistingAssets(fixture),
      providerQueries: plan.plannedProviderQueries.map((query) => ({ ...query })),
      collection: {
        reused: collectionGroups.reused_frozen ?? [],
        awaitingProbe: collectionGroups.awaiting_capability_probe ?? [],
        blocked: collectionGroups.blocked_permission ?? [],
        running: collectionGroups.running ?? [],
      },
      pendingResults: buildPendingResults(fixture),
      gaps: [
        ...plan.currentGapFactorIds.map((id) => ({ id, type: "factor", label: factors.get(id).label, scope: "当前案例因素", status: "open" })),
        ...plan.positiveEvidenceSearches.map((item) => ({ ...item, type: "positive" })),
        ...plan.negativeEvidenceSearches.map((item) => ({ ...item, type: "negative" })),
      ],
      failures: fixture.providerRuns
        .filter((run) => failureOutcomes.has(run.outcome))
        .map((run) => ({ ...run })),
      manualUploads: fixture.providerRuns
        .filter((run) => run.outcome === "manual_upload")
        .map((run) => ({ ...run })),
      resultMetricIds: [...plan.resultMetricIds],
    };
  }

  window.RESEARCH_PLAN_STATE = Object.freeze({ buildResearchPlanViewModel });
}());
