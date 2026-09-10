import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import {
  EventWorkflowError,
  isActiveWorkflowState,
  type EventWorkflow,
  type EventWorkflowClient,
  type CaseRuntimeStatus,
  type ScopeDecisionInput,
  type WorkflowLedgerItem,
} from "../../domain/eventWorkflow";
import { RuntimeStatusNotice } from "../system/RuntimeStatusNotice";

const POLL_INTERVAL_MS = 5_000;

const STAGE_STATUS_LABEL: Record<EventWorkflow["stages"][number]["status"], string> = {
  pending: "待开始",
  active: "进行中",
  completed: "已完成",
  failed: "失败",
  recovering: "恢复中",
  blocked: "等待决定",
  cancelled: "已取消",
};

const LEDGER_STATUS_LABEL: Record<WorkflowLedgerItem["status"], string> = {
  search_candidate: "搜索候选",
  fetching: "获取中",
  frozen: "已冻结",
  deduplicated: "重复合并",
  conflicted: "变体冲突",
  admitted: "已准入",
  quarantined: "已隔离",
  skipped: "已跳过",
};

const SOURCE_ROLE_LABEL: Record<string, string> = {
  primary_disclosure: "公司或监管机构原始披露",
  independent_reporting: "独立媒体报道",
  expert_analysis: "专业分析",
  market_data: "市场数据",
  user_upload: "用户上传资料",
};

const EVIDENCE_ROLE_LABEL: Record<string, string> = {
  supports: "支持当前命题",
  contradicts: "反驳当前命题",
  context: "提供研究背景",
  neutral: "中性参考资料",
};

const ADMISSION_LABEL: Record<string, string> = {
  automatically_admitted: "系统规则准入",
  human_admitted: "人工确认准入",
  admitted: "已准入研究",
  pending_review: "等待准入审核",
  quarantined: "已隔离待核验",
  rejected: "未准入研究",
};

const MAPPING_LABEL: Record<string, string> = {
  mapped: "已映射到证据目标",
  merged: "已合并到已有资料",
  unmapped: "尚未映射到证据目标",
};

const DEDUP_LABEL: Record<string, string> = {
  same_content: "与已冻结资料内容一致",
  canonical_match: "与已有规范资料一致",
  variant_content: "检测到内容变体",
  no_match: "未发现重复资料",
};

const RECOVERY_STATUS_LABEL: Record<string, string> = {
  healthy: "运行正常",
  stale: "心跳已过期",
  recovering: "正在恢复",
  failed: "恢复失败",
  idle: "当前空闲",
};

const COVERAGE_STATUS_LABEL: Record<string, string> = {
  continue: "继续补证",
  ready: "覆盖达标",
  blocked: "等待处理",
  exhausted: "检索已充分",
  failed: "评估失败",
};

function productStatus(value: string | null, labels: Record<string, string>): string {
  if (!value) return "尚未记录";
  return labels[value] ?? "状态已记录";
}

function dateTime(value: string | null): string {
  if (!value) return "未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function durationSince(value: string | null): string {
  if (!value) return "持续时间未记录";
  const elapsed = Math.max(0, Date.now() - new Date(value).getTime());
  if (!Number.isFinite(elapsed)) return "持续时间未记录";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "不足 1 分钟";
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

function heartbeatLabel(workflow: EventWorkflow): { status: "fresh" | "stale" | "unknown"; text: string } {
  const heartbeat = workflow.systemAction.heartbeatAt ?? workflow.recovery.workerHeartbeatAt;
  if (!heartbeat) {
    return { status: "unknown", text: "心跳待确认，服务端尚未记录运行心跳" };
  }
  const stale = workflow.systemAction.recoveryStatus === "stale"
    || workflow.recovery.status === "stale";
  return {
    status: stale ? "stale" : "fresh",
    text: stale ? `心跳已过期，上次记录 ${dateTime(heartbeat)}` : `心跳正常，更新于 ${dateTime(heartbeat)}`,
  };
}

function recommendationKind(action: NonNullable<EventWorkflow["userAction"]>): ScopeDecisionInput["kind"] | null {
  const kind = action.recommendation.kind;
  return kind === "stop" || kind === "keep_scope" ? kind : null;
}

function recommendsScopeRevision(action: NonNullable<EventWorkflow["userAction"]>): boolean {
  return action.recommendation.kind === "revise_scope";
}

function recommendsProtocolCompletion(action: NonNullable<EventWorkflow["userAction"]>): boolean {
  return action.recommendation.kind === "complete_research_protocol";
}

function alternativeDecision(value: Record<string, unknown>): {
  kind: ScopeDecisionInput["kind"];
  label: string;
  impact: string;
} | null {
  const kind = value.kind;
  const label = value.label;
  const impact = value.impact;
  if (
    (kind !== "keep_scope" && kind !== "stop")
    || typeof label !== "string"
    || !label.trim()
    || typeof impact !== "string"
    || !impact.trim()
  ) return null;
  return { kind, label, impact };
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "无法序列化此记录";
  }
}

function LazyTechnicalDetails({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="event-workflow-technical"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>技术详情</summary>
      {open && <pre>{safeJson(value)}</pre>}
    </details>
  );
}

function LedgerRow({ item }: { item: WorkflowLedgerItem }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const admission = item.admissionOutcome ?? item.reviewState;
  const finalUrl = item.finalUrl ?? item.sourceUrl;
  return (
    <article className="event-workflow-ledger__row">
      <header>
        <span className="event-workflow-status">{LEDGER_STATUS_LABEL[item.status]}</span>
        <strong>{item.reason}</strong>
        <time dateTime={item.recordedAt}>{dateTime(item.recordedAt)}</time>
      </header>
      <dl>
        <div><dt>来源类型</dt><dd>{productStatus(item.sourceRole, SOURCE_ROLE_LABEL)}</dd></div>
        <div><dt>证据用途</dt><dd>{productStatus(item.role, EVIDENCE_ROLE_LABEL)}</dd></div>
        <div><dt>准入状态</dt><dd>{productStatus(admission, ADMISSION_LABEL)}</dd></div>
        <div><dt>资料处理</dt><dd>{item.dedupRelation ? productStatus(item.dedupRelation, DEDUP_LABEL) : productStatus(item.mappingDisposition, MAPPING_LABEL)}</dd></div>
      </dl>
      <details onToggle={(event) => setDetailsOpen(event.currentTarget.open)}>
        <summary>查看获取与溯源详情</summary>
        {detailsOpen && (
          <div className="event-workflow-ledger__technical">
            <pre>{safeJson({
              record_id: item.recordId,
              record_type: item.recordType,
              status: item.status,
              reason_code: item.reasonCode,
              adapter_key: item.adapterKey,
              attempt_id: item.attemptId,
              source_role: item.sourceRole,
              evidence_role: item.role,
              source_url: item.sourceUrl,
              final_url: item.finalUrl,
              retrieved_at: item.retrievedAt,
              content_sha256: item.contentSha256,
              publication_key: item.publicationKey,
              dedup_relation: item.dedupRelation,
              admission_outcome: item.admissionOutcome,
              review_state: item.reviewState,
              mapping_disposition: item.mappingDisposition,
              mapping_kind: item.mappingKind,
              evidence_link_id: item.evidenceLinkId,
            })}</pre>
            {finalUrl && <a href={finalUrl} rel="noreferrer" target="_blank">打开最终来源</a>}
            {item.drilldown ? item.drilldown.href.startsWith("/events/") ? (
              <Link to={item.drilldown.href}>查看可验证记录</Link>
            ) : (
              <a href={item.drilldown.href}>查看可验证记录</a>
            ) : (
              <small>{item.drilldownUnavailableReason ?? "当前记录没有可用下钻入口"}</small>
            )}
          </div>
        )}
      </details>
    </article>
  );
}

export function EventWorkflowOverview({
  caseId,
  client = researchClient,
}: {
  caseId: string;
  client?: EventWorkflowClient;
}) {
  const [workflow, setWorkflow] = useState<EventWorkflow | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [ledgerItems, setLedgerItems] = useState<WorkflowLedgerItem[] | null>(null);
  const [ledgerCursor, setLedgerCursor] = useState<string | null>(null);
  const [ledgerHasMore, setLedgerHasMore] = useState(false);
  const [ledgerBusy, setLedgerBusy] = useState(false);
  const [decisionBusy, setDecisionBusy] = useState<{ caseId: string; generation: number } | null>(null);
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const [runtimeStatus, setRuntimeStatus] = useState<CaseRuntimeStatus | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeRetrying, setRuntimeRetrying] = useState(false);
  const [runtimeRefreshEpoch, setRuntimeRefreshEpoch] = useState(0);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const ledgerRequest = useRef<AbortController | null>(null);
  const decisionRequest = useRef<AbortController | null>(null);
  const runtimeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastCaseId = useRef<string | null>(null);

  useEffect(() => {
    if (!workflow || !client.getCaseRuntimeStatus) return;
    const controller = new AbortController();
    let disposed = false;
    setRuntimeRetrying(true);
    void client.getCaseRuntimeStatus(caseId, { signal: controller.signal })
      .then((next) => {
        if (disposed || controller.signal.aborted) return;
        setRuntimeStatus(next);
        setRuntimeError(null);
        if (workflow && (isActiveWorkflowState(workflow.state) || workflow.state === "monitoring")) {
          runtimeTimer.current = setTimeout(
            () => setRuntimeRefreshEpoch((value) => value + 1),
            POLL_INTERVAL_MS,
          );
        }
      })
      .catch((reason: unknown) => {
        if (disposed || controller.signal.aborted) return;
        const typed = reason as { message?: string };
        setRuntimeStatus(null);
        setRuntimeError(typed.message || "运行状态接口暂时无法读取");
        if (workflow && (isActiveWorkflowState(workflow.state) || workflow.state === "monitoring")) {
          runtimeTimer.current = setTimeout(
            () => setRuntimeRefreshEpoch((value) => value + 1),
            POLL_INTERVAL_MS,
          );
        }
      })
      .finally(() => {
        if (!disposed && !controller.signal.aborted) setRuntimeRetrying(false);
      });
    return () => {
      disposed = true;
      controller.abort();
      if (runtimeTimer.current) clearTimeout(runtimeTimer.current);
      runtimeTimer.current = null;
    };
  }, [caseId, client, runtimeRefreshEpoch, workflow?.state, workflow?.version]);

  useEffect(() => {
    const currentGeneration = ++generation.current;
    const caseChanged = lastCaseId.current !== caseId;
    lastCaseId.current = caseId;
    let disposed = false;

    const clearPoll = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
    const load = async () => {
      clearPoll();
      activeRequest.current?.abort();
      const controller = new AbortController();
      activeRequest.current = controller;
      try {
        const next = await client.getEventWorkflow(caseId, { signal: controller.signal });
        if (disposed || generation.current !== currentGeneration) return;
        setWorkflow(next);
        setError(null);
        if (isActiveWorkflowState(next.state)) {
          timer.current = setTimeout(() => { void load(); }, POLL_INTERVAL_MS);
        }
      } catch (reason) {
        if (disposed || controller.signal.aborted || generation.current !== currentGeneration) return;
        const typed = reason as { code?: string; message?: string };
        setWorkflow(null);
        setError({
          code: typed.code ?? (reason instanceof EventWorkflowError ? reason.code : "workflow_unavailable"),
          message: typed.message || "无法读取统一研究流程，请检查网络或服务状态后重试。",
        });
      }
    };

    if (caseChanged) {
      setWorkflow(null);
      setError(null);
      setLedgerItems(null);
      setLedgerCursor(null);
      setLedgerHasMore(false);
      setLedgerBusy(false);
      decisionRequest.current?.abort();
      decisionRequest.current = null;
      setDecisionBusy(null);
      setRuntimeStatus(null);
      setRuntimeError(null);
    }
    void load();
    return () => {
      disposed = true;
      clearPoll();
      activeRequest.current?.abort();
      ledgerRequest.current?.abort();
      decisionRequest.current?.abort();
    };
  }, [caseId, client, refreshEpoch]);

  const loadLedger = useCallback(async (append: boolean) => {
    if (ledgerBusy) return;
    setLedgerBusy(true);
    ledgerRequest.current?.abort();
    const controller = new AbortController();
    ledgerRequest.current = controller;
    const requestGeneration = generation.current;
    try {
      const page = await client.getEventWorkflowLedger(caseId, {
        ...(append && ledgerCursor ? { cursor: ledgerCursor } : {}),
        limit: 50,
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation.current !== requestGeneration) return;
      setLedgerItems((current) => append ? [...(current ?? []), ...page.items] : page.items);
      setLedgerCursor(page.nextCursor);
      setLedgerHasMore(page.hasMore);
    } catch (reason) {
      if (!controller.signal.aborted && generation.current === requestGeneration) {
        const typed = reason as { message?: string };
        setError({ code: "workflow_ledger_unavailable", message: typed.message || "完整资料台账暂时无法读取。" });
      }
    } finally {
      if (ledgerRequest.current === controller) setLedgerBusy(false);
    }
  }, [caseId, client, ledgerBusy, ledgerCursor]);

  async function submitDecision(requestedKind?: ScopeDecisionInput["kind"]) {
    const commandGeneration = generation.current;
    const busyForCurrentCase = decisionBusy?.caseId === caseId
      && decisionBusy.generation === commandGeneration;
    if (!workflow?.userAction || busyForCurrentCase) return;
    const action = workflow.userAction;
    const kind = requestedKind ?? recommendationKind(action);
    if (!kind) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    activeRequest.current?.abort();
    decisionRequest.current?.abort();
    const commandController = new AbortController();
    decisionRequest.current = commandController;
    setDecisionBusy({ caseId, generation: commandGeneration });
    try {
      await client.decideEventScope(caseId, {
        kind,
        reason: action.reason,
        expectedVersion: workflow.version,
        idempotencyKey: `workflow:scope-decision:${workflow.orchestrationId}:v${workflow.version}:${kind}`,
      }, { signal: commandController.signal });
      if (generation.current !== commandGeneration) return;

      const controller = new AbortController();
      activeRequest.current = controller;
      try {
        const latest = await client.getEventWorkflow(caseId, { signal: controller.signal });
        if (controller.signal.aborted || generation.current !== commandGeneration) return;
        setWorkflow(latest);
        setError(null);
        if (isActiveWorkflowState(latest.state)) {
          timer.current = setTimeout(
            () => setRefreshEpoch((value) => value + 1),
            POLL_INTERVAL_MS,
          );
        }
      } catch (reason) {
        if (controller.signal.aborted || generation.current !== commandGeneration) return;
        const typed = reason as { message?: string };
        setWorkflow(null);
        setError({
          code: "workflow_refresh_failed_after_decision",
          message: typed.message
            ? `决定已提交但最新流程暂不可读取：${typed.message}`
            : "决定已提交但最新流程暂不可读取。",
        });
      }
    } catch (reason) {
      if (commandController.signal.aborted || generation.current !== commandGeneration) return;
      const typed = reason as { code?: string; message?: string };
      setError({ code: typed.code ?? "scope_decision_failed", message: typed.message || "研究边界决定未提交，请重试。" });
    } finally {
      if (
        decisionRequest.current === commandController
        && generation.current === commandGeneration
        && caseId === lastCaseId.current
      ) {
        decisionRequest.current = null;
        setDecisionBusy(null);
      }
    }
  }

  if (error && !workflow) {
    return (
      <section className="event-workflow-error" role="alert" data-error-code={error.code}>
        <strong>统一研究流程无法读取</strong>
        <p>{error.message}</p>
        <p>页面不会使用旧 Case 状态替代真实运行详情。</p>
        <button type="button" onClick={() => setRefreshEpoch((value) => value + 1)}>
          重新读取最新流程
        </button>
      </section>
    );
  }
  if (!workflow) {
    return <section className="event-workflow-loading" aria-busy="true" aria-live="polite">正在读取当前事件的统一研究流程…</section>;
  }

  const heartbeat = heartbeatLabel(workflow);
  const visibleLedger = ledgerItems ?? workflow.sourceLedger.items;
  const recommendationLabel = typeof workflow.userAction?.recommendation.label === "string"
    ? workflow.userAction.recommendation.label
    : workflow.userAction?.label;
  const supportedDecisionKind = workflow.userAction ? recommendationKind(workflow.userAction) : null;
  const scopeRevisionRecommended = workflow.userAction ? recommendsScopeRevision(workflow.userAction) : false;
  const protocolCompletionRecommended = workflow.userAction ? recommendsProtocolCompletion(workflow.userAction) : false;
  const alternativeDecisions = workflow.userAction
    ? workflow.userAction.alternatives
      .map(alternativeDecision)
      .filter((item): item is NonNullable<typeof item> => item !== null)
      .filter((item) => item.kind !== supportedDecisionKind)
    : [];
  const recovering = workflow.state === "recovering"
    || workflow.systemAction.recoveryStatus === "recovering"
    || workflow.recovery.status === "recovering";
  const decisionBusyForCurrentCase = decisionBusy?.caseId === caseId
    && decisionBusy.generation === generation.current;

  return (
    <section className="event-workflow" aria-label="当前事件研究流程">
      <header className="event-workflow__context">
        <div>
          <p className="event-workflow-kicker">统一研究流程 · Case {workflow.event.caseId}</p>
          <h2 className="event-workflow__event-title">{workflow.event.title}</h2>
          <p>{workflow.event.researchQuestion}</p>
        </div>
        <Link to={`/events/${encodeURIComponent(caseId)}/scope`}>查看命题与冻结范围 v{workflow.scope.version}</Link>
      </header>

      <ol className="event-workflow-stages" aria-label="事件研究六阶段">
        {workflow.stages.map((stage, index) => (
          <li className={`is-${stage.status}`} key={stage.code} aria-current={stage.status === "active" ? "step" : undefined}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{stage.displayName}</strong>
            <small>{STAGE_STATUS_LABEL[stage.status]}：{stage.reason}</small>
          </li>
        ))}
      </ol>

      <div className="event-workflow-workspaces">
        <section className="event-workflow-user" aria-label="你现在应该做什么" aria-live="polite">
          <p className="event-workflow-kicker">你现在应该做什么</p>
          <h3 id="event-workflow-user-title">{workflow.userAction?.label ?? workflow.userActionSummary}</h3>
          {workflow.userAction ? (
            <>
              <p>{workflow.userAction.reason}</p>
              <dl>
                <div><dt>系统建议</dt><dd>{recommendationLabel}</dd></div>
                <div><dt>影响</dt><dd>{workflow.userAction.impact}</dd></div>
              </dl>
              {scopeRevisionRecommended ? (
                <Link className="event-workflow-primary-action" to={`/events/${encodeURIComponent(caseId)}/scope`}>
                  调整研究范围
                </Link>
              ) : protocolCompletionRecommended ? (
                <Link className="event-workflow-primary-action" to={`/events/${encodeURIComponent(caseId)}/protocol?focus=required`}>
                  完成研究协议
                </Link>
              ) : supportedDecisionKind ? (
                <button className="event-workflow-primary-action" type="button" disabled={decisionBusyForCurrentCase} onClick={() => void submitDecision()}>
                  {decisionBusyForCurrentCase ? "正在提交决定…" : recommendationLabel}
                </button>
              ) : (
                <p className="event-workflow-inline-error" role="alert">系统建议无法映射为受支持的范围决定，请刷新或联系管理员。</p>
              )}
              {alternativeDecisions.length > 0 && (
                <details>
                  <summary>查看其他选择及影响</summary>
                  <ul className="event-workflow-alternatives">
                    {alternativeDecisions.map((alternative) => (
                      <li key={alternative.kind}>
                        <div><strong>{alternative.label}</strong><span>{alternative.impact}</span></div>
                        <button
                          className="event-workflow-alternative-action"
                          disabled={decisionBusyForCurrentCase}
                          onClick={() => void submitDecision(alternative.kind)}
                          type="button"
                        >
                          {decisionBusyForCurrentCase ? "正在提交决定…" : alternative.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          ) : (
            <p>系统会按已冻结范围自动推进，你可以离开页面，稍后回来查看过程。</p>
          )}
        </section>

        <section className="event-workflow-system" aria-label="系统正在做什么" aria-live="polite">
          <p className="event-workflow-kicker">系统正在做什么</p>
          <h3 id="event-workflow-system-title">{workflow.systemAction.label ?? "当前阶段没有运行中的系统动作"}</h3>
          <p>{workflow.systemAction.reason ?? "本阶段原因尚未记录。"}</p>
          <dl className="event-workflow-system__meta">
            <div><dt>状态开始</dt><dd>{dateTime(workflow.systemAction.startedAt)}</dd></div>
            <div><dt>已持续</dt><dd>{durationSince(workflow.systemAction.startedAt)}</dd></div>
            <div><dt>运行心跳</dt><dd className={`is-${heartbeat.status}`}>{heartbeat.text}</dd></div>
            <div><dt>恢复状态</dt><dd>{productStatus(workflow.systemAction.recoveryStatus ?? workflow.recovery.status, RECOVERY_STATUS_LABEL)}</dd></div>
          </dl>
          <LazyTechnicalDetails value={{
            orchestration_id: workflow.orchestrationId,
            research_run_id: workflow.researchRunId,
            research_execution: workflow.researchExecution,
            scope_version_id: workflow.scope.id,
            scope_version: workflow.scope.version,
            query_plan_ids: workflow.acquisition.rounds.map((round) => round.queryPlanId),
            recovery_status: workflow.systemAction.recoveryStatus ?? workflow.recovery.status,
            recovery_source: workflow.recovery.source,
          }} />
          {client.getCaseRuntimeStatus && (
            <RuntimeStatusNotice
              status={runtimeStatus}
              error={runtimeError}
              retrying={runtimeRetrying}
              fallbackCheckpoint={{
                version: workflow.version,
                savedAt: workflow.updatedAt,
              }}
              onRetry={() => setRuntimeRefreshEpoch((value) => value + 1)}
            />
          )}
          {recovering && <p className="event-workflow-recovery">系统正在按检查点恢复。{workflow.recovery.reason ? ` 原因：${workflow.recovery.reason}` : "服务端已标记恢复中，正在等待下一次心跳。"}</p>}
          {heartbeat.status === "stale" && !recovering && <p className="event-workflow-recovery">运行心跳已过期，恢复状态尚未由服务端确认。</p>}
        </section>
      </div>

      {error && <p className="event-workflow-inline-error" role="alert">{error.message}</p>}

      <section className="event-workflow-process" aria-labelledby="event-workflow-process-title">
        <header><div><p className="event-workflow-kicker">最近过程</p><h3 id="event-workflow-process-title">系统做过什么</h3></div><span>不可变事件序列 #{workflow.events[0]?.sequence ?? 0}</span></header>
        <ol>
          {workflow.events.map((event) => (
            <li key={event.id}>
              <time dateTime={event.createdAt}>{dateTime(event.createdAt)}</time>
              <div><strong>{event.message}</strong><small>过程记录 #{event.sequence}</small></div>
              <LazyTechnicalDetails value={{ transition: event.transition, actor: event.actor, payload: event.payload }} />
            </li>
          ))}
        </ol>
        {workflow.events.length === 0 && <p className="event-workflow-empty">流程刚刚建立，尚未记录过程事件。</p>}
      </section>

      <section className="event-workflow-coverage" aria-labelledby="event-workflow-coverage-title">
        <header><div><p className="event-workflow-kicker">目标覆盖</p><h3 id="event-workflow-coverage-title">不是按资料数量判定完成</h3></div><span>{workflow.coverage.length} 个证据目标</span></header>
        <div>
          {workflow.coverage.map((goal) => (
            <article key={goal.goalId}>
              <header><strong>{goal.objective}</strong><span>{COVERAGE_STATUS_LABEL[goal.status] ?? "状态已记录"}</span></header>
              <p>权威来源 {goal.observedAuthorityCount}/{goal.requiredAuthorityCount} · 独立来源 {goal.observedIndependentSourceCount}/{goal.requiredIndependentSourceCount} · 反证搜索{goal.contrarySearchCompleted ? "已完成" : "未完成"}</p>
              {(goal.unresolved.length > 0 || goal.unknown.length > 0) && <small>未解决：{[...goal.unresolved, ...goal.unknown].map(String).join("；")}</small>}
            </article>
          ))}
        </div>
        {workflow.coverage.length === 0 && <p className="event-workflow-empty">资料获取计划尚未生成证据目标。</p>}
      </section>

      {workflow.conclusion && (
        <section className="event-workflow-conclusion" aria-label="系统研究结论">
          <header><p className="event-workflow-kicker">当前报告</p><span>{workflow.conclusion.reviewLabel}</span></header>
          <p>{workflow.conclusion.text}</p>
          {workflow.monitor && <small>持续监测：{workflow.monitor.status} · 下一验证事件：{workflow.monitor.nextVerificationEvent}</small>}
        </section>
      )}

      <section className="event-workflow-ledger" aria-label="来源资料台账" data-responsive="stack">
        <header>
          <div><p className="event-workflow-kicker">来源资料台账</p><h3>每一次搜索、冻结、去重和准入都有记录</h3></div>
          <dl>
            <div><dt>总记录</dt><dd>{workflow.sourceLedger.counts.total}</dd></div>
            <div><dt>自动准入</dt><dd>{workflow.sourceLedger.counts.automaticallyAdmitted}</dd></div>
            <div><dt>重复合并</dt><dd>{workflow.sourceLedger.counts.deduplicated}</dd></div>
            <div><dt>隔离</dt><dd>{workflow.sourceLedger.counts.quarantined}</dd></div>
          </dl>
        </header>
        <div className="event-workflow-ledger__items">
          {visibleLedger.map((item) => <LedgerRow item={item} key={item.recordId} />)}
        </div>
        {visibleLedger.length === 0 && !workflow.sourceLedger.hasMore && (
          <p className="event-workflow-empty">尚未记录搜索候选或冻结资料，系统开始获取后会在这里留下可追溯记录。</p>
        )}
        {ledgerItems === null && workflow.sourceLedger.hasMore && (
          <button type="button" className="event-workflow-secondary-action" disabled={ledgerBusy} onClick={() => void loadLedger(false)}>
            {ledgerBusy ? "正在读取完整台账…" : "展开完整资料台账"}
          </button>
        )}
        {ledgerItems !== null && ledgerHasMore && (
          <button type="button" className="event-workflow-secondary-action" disabled={ledgerBusy} onClick={() => void loadLedger(true)}>
            {ledgerBusy ? "正在读取下一页…" : "继续加载资料台账"}
          </button>
        )}
      </section>
    </section>
  );
}
