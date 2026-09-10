import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";

import { GatewayHttpError } from "@/gateway/HttpGatewayClient";
import type {
  GatewayConnectionState,
  GatewayConversationClient,
  GatewayConversationSnapshot,
  GatewayNativeRunStatus,
  GatewayRejectedIntentReceipt,
  GatewayRole,
  GatewayRoleStatus,
  GatewaySafeReasonCode,
  GatewayTeamProgress,
  GatewaySession,
} from "@/gateway/contracts";
import { hasTeamClient, professionalRoleLabels, professionalRoles, type ProfessionalRole, type TeamCitation, type TeamOutput } from "@/gateway/team";
import type { GatewayConversationIssue } from "@/gateway/useGatewayConversation";
import { useGatewayTeam } from "@/gateway/useGatewayTeam";
import { useTeamMutation } from "@/gateway/useTeamMutation";

import { ProfessionalRoleFrame } from "./ProfessionalRoleFrame";
import { ProfessionalTeamWorkspace } from "./ProfessionalTeamWorkspace";
import { createPortal } from "react-dom";
import { tasksForRevision } from "@/gateway/team";
import { GatewayResearchContent } from "./GatewayResearchContent";

const roleLabels: Record<GatewayRole, string> = {
  scope_identity: "范围与身份",
  sources_evidence: "资料与证据",
  analysis_counter_evidence: "分析与反证",
  compilation_checks: "汇编与检查",
};

const roleStatusLabels: Record<GatewayRoleStatus, string> = {
  queued: "等待执行",
  running: "执行中",
  blocked: "已阻塞",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};

const nativeStatusLabels: Record<GatewayNativeRunStatus, string> = {
  queued: "等待执行",
  running: "执行中",
  waiting_for_sources: "等待资料来源",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};

const reasonLabels: Record<GatewaySafeReasonCode, string> = {
  unsupported_in_gateway_p0: "当前请求不在 Gateway P0 支持范围内",
  native_execution_failed: "底层执行未能完成",
  authorization_required: "需要重新验证访问权限",
  cutoff_unavailable: "无法确认资料截止口径",
  source_policy_blocked: "资料来源不符合当前策略",
  source_unavailable: "等待可用的合规资料来源",
  evidence_gap: "仍存在待补充的证据缺口",
  validation_failed: "检查尚未通过",
  command_rejected: "该控制请求未被接受",
};

const connectionLabels: Record<GatewayConnectionState, string> = {
  connecting: "正在连接执行流",
  live: "执行流已连接",
  reconnecting: "正在恢复执行流",
  paused: "执行流已暂停",
  access_revoked: "访问已撤销",
};

type GatewayConversationPanelProps = {
  client: GatewayConversationClient;
  snapshot: GatewayConversationSnapshot | null;
  connection: GatewayConnectionState;
  issue: GatewayConversationIssue;
  onRefresh: () => void;
  onConversationChanged: () => void;
  onNotice?: (notice: string) => void;
  onAuthorizationLost: () => void;
  session: GatewaySession;
  teamProgressByRun?: Record<string, GatewayTeamProgress>;
  initialRunSpecId?: string;
  initialTeamRevision?: number;
};

type PendingMessage = {
  idempotencyKey: string;
  text: string;
};

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

function isRejectedIntent(
  receipt: Awaited<ReturnType<GatewayConversationClient["sendMessage"]>>,
): receipt is GatewayRejectedIntentReceipt {
  return "receiptKind" in receipt;
}

export function GatewayConversationPanel({
  client,
  snapshot,
  connection,
  issue,
  onRefresh,
  onConversationChanged,
  onNotice,
  onAuthorizationLost,
  session,
  teamProgressByRun,
  initialRunSpecId,
  initialTeamRevision,
}: GatewayConversationPanelProps): ReactElement {
  const [privateCleared, setPrivateCleared] = useState(false);
  const [titleExpanded, setTitleExpanded] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [recipientRequest, setRecipientRequest] = useState<{ role: ProfessionalRole; key: number } | null>(null);
  const [selectedRunSpecId, setSelectedRunSpecId] = useState<string | null>(initialRunSpecId ?? null);
  const [selectedTeamRevision, setSelectedTeamRevision] = useState<number | undefined>(Number.isSafeInteger(initialTeamRevision) && initialTeamRevision! > 0 ? initialTeamRevision : undefined);
  const [teamCitationRequest, setTeamCitationRequest] = useState<{ citation: TeamCitation; output: TeamOutput; focusReturn: HTMLElement | null; key: number } | null>(null);
  const [roleEvidenceRequest, setRoleEvidenceRequest] = useState<{ key: number; label: string; ids: string[] } | null>(null);
  const previousTeamRevision = useRef<number | undefined>(undefined);
  const requestedTeamProgress = useRef("");
  const loseAuthorization = useCallback(() => { setPrivateCleared(true); onAuthorizationLost(); }, [onAuthorizationLost]);
  const runs = snapshot?.runs ?? [];
  const latestRun = runs.at(-1) ?? null;
  const selectedRun = runs.find((run) => run.runSpecId === selectedRunSpecId) ?? latestRun;
  const selectedRunSpec = selectedRun?.runSpecId ?? "";
  const teamSurfaceEnabled = teamProgressByRun !== undefined;
  const teamEnabled = hasTeamClient(client);
  const selectedRunProgress = selectedRunSpec ? teamProgressByRun?.[selectedRunSpec] : undefined;
  const authorizedArtifactsKey = selectedRun ? JSON.stringify([selectedRun.runSpecId, [...new Set((snapshot?.events ?? []).filter((event) => event.runSpecId === selectedRun.runSpecId).flatMap((event) => event.artifacts
    .filter((artifact) => artifact.kind === "evidence_link" || artifact.kind === "draft")
    .map((artifact) => `${artifact.kind}:${artifact.id}`)))].sort()]) : "";
  const team = useGatewayTeam({ client, conversationId: snapshot?.conversationId ?? "", runSpecId: selectedRunSpec,
    authorizedArtifactsKey, onAuthorizationLost: loseAuthorization, enabled: Boolean(snapshot && selectedRun && teamSurfaceEnabled) });
  const teamMutation = useTeamMutation({ client, conversationId: snapshot?.conversationId ?? "", runSpecId: selectedRunSpec, principal: session,
    onConfirmed: () => { team.refresh(); onConversationChanged(); onRefresh(); }, onAuthorizationLost: loseAuthorization, onInvalidated: team.invalidate });

  useEffect(() => {
    if (!snapshot) return;
    if (initialRunSpecId && !runs.some(run => run.runSpecId === initialRunSpecId)) return;
    if (!latestRun) {
      setSelectedRunSpecId(null);
      setSelectedTeamRevision(undefined);
      previousTeamRevision.current = undefined;
      requestedTeamProgress.current = "";
      setTeamCitationRequest(null);
      return;
    }
    if (!selectedRunSpecId || !runs.some((run) => run.runSpecId === selectedRunSpecId)) {
      setSelectedRunSpecId(latestRun.runSpecId);
      setSelectedTeamRevision(undefined);
      previousTeamRevision.current = undefined;
      requestedTeamProgress.current = "";
      setTeamCitationRequest(null);
    }
  }, [latestRun?.runSpecId, selectedRunSpecId, runs, selectedRun]);

  useEffect(() => {
    if (team.state.kind !== "available") return;
    const revision = team.state.data.revision;
    const previous = previousTeamRevision.current;
    previousTeamRevision.current = revision;
    setSelectedTeamRevision((current) => {
      if (initialTeamRevision !== undefined && current !== undefined) return current;
      if (current === undefined || current === previous) return revision || undefined;
      return current <= revision ? current : revision || undefined;
    });
  }, [team.state]);

  useEffect(() => {
    if (!selectedRunProgress || team.state.kind !== "available") return;
    const visible = team.state.data;
    const progressKey = `${selectedRunProgress.runSpecId}/${selectedRunProgress.revision}/${selectedRunProgress.eventSequence}`;
    if (visible.revision < selectedRunProgress.revision || visible.event_sequence < selectedRunProgress.eventSequence) {
      if (requestedTeamProgress.current !== progressKey) {
        requestedTeamProgress.current = progressKey;
        team.refresh();
      }
    } else if (requestedTeamProgress.current === progressKey) requestedTeamProgress.current = "";
  }, [selectedRunProgress, team.state, team.refresh]);
  if (issue === "access_revoked" || issue === "auth_required" || privateCleared) {
    return (
      <section className="gateway-private-state" aria-label="研究访问状态">
        <p role="alert">访问已撤销，已清除当前私有研究内容。</p>
      </section>
    );
  }

  if (!snapshot) {
    return (
      <section className="gateway-private-state" aria-label="研究访问状态">
        <p role="status">正在加载安全研究投影…</p>
        <IssueNotice issue={issue} onRefresh={onRefresh} />
      </section>
    );
  }

  if ((initialRunSpecId && !runs.some(run => run.runSpecId === initialRunSpecId)) ||
      (initialTeamRevision && team.state.kind === 'available' && initialTeamRevision > team.state.data.revision)) {
    return <section className="gateway-private-state" aria-label="历史研究版本"><p role="alert">该公司版本关联的研究轮次或团队版本已不可访问，请返回公司档案核对。</p></section>;
  }

  const messageHistory = (<div className="gateway-conversation__timeline" aria-live="polite">
        <section className="gateway-message-list" aria-labelledby="gateway-message-heading">
          <header className="gateway-section-heading">
            <h2 id="gateway-message-heading">已确认的研究请求</h2>
            <span>仅显示已保存内容</span>
          </header>
          {snapshot.messages.length === 0 ? (
            <p className="gateway-empty-copy">尚无可显示的研究请求。</p>
          ) : (
            <ol>
              {[...snapshot.messages]
                .sort((left, right) => left.sequence - right.sequence)
                .map((message) => (
                  <li
                    className={`gateway-message gateway-message--${message.kind ?? "user"}`}
                    key={message.id}
                  >
                    <span>{message.kind === "system" ? "Gateway" : "研究员"}</span>
                    <p>{message.text}</p>
                    <time dateTime={message.createdAt}>{formatTime(message.createdAt)}</time>
                  </li>
                ))}
            </ol>
          )}
        </section>

        <section className="gateway-event-list" aria-labelledby="gateway-event-heading">
          <header className="gateway-section-heading">
            <h2 id="gateway-event-heading">安全执行记录</h2>
            <span>不等同于专业角色分工</span>
          </header>
          {snapshot.events.length === 0 ? (
            <p className="gateway-empty-copy">等待 Gateway 报告第一个安全执行阶段。</p>
          ) : (
            <details className="gateway-history-disclosure" open={historyOpen} onToggle={(event) => setHistoryOpen(event.currentTarget.open)}>
              <summary>展开完整执行记录（{snapshot.events.length} 条）</summary>
            {historyOpen ? <ol>
              {[...snapshot.events]
                .sort((left, right) => left.sequence - right.sequence)
                .map((event) => (
                  <li className="gateway-event" key={event.sequence}>
                    <time dateTime={event.occurredAt}>{formatTime(event.occurredAt)}</time>
                    <article>
                      <header>
                        <h3>{roleLabels[event.role]}</h3>
                        {event.status ? (
                          <span className={`gateway-status gateway-status--${event.status}`}>
                            {roleStatusLabels[event.status]}
                          </span>
                        ) : null}
                      </header>
                      {event.summary ? <p>{event.summary}</p> : null}
                      {event.reasonCode ? (
                        <small>说明：{reasonLabels[event.reasonCode]}</small>
                      ) : null}
                      {event.artifacts.length > 0 ? (
                        <small>
                          安全工件引用 {event.artifacts.length} 项
                          {event.artifacts.some((artifact) => artifact.locatorAvailable)
                            ? " · 可在获授权环境中定位"
                            : ""}
                        </small>
                      ) : null}
                    </article>
                  </li>
                ))}
            </ol> : null}
            </details>
          )}
        </section>
      </div>);
  const composer = (<GatewayMessageComposer
        key={selectedRun?.runSpecId ?? "unbound"}
        recipientRequest={recipientRequest}
        readOnly={teamSurfaceEnabled && team.state.kind === "available" && selectedTeamRevision !== undefined && selectedTeamRevision !== team.state.data.revision}
        teamStale={team.state.kind === "available" && Boolean(team.state.stale)}
        client={client}
        conversationId={snapshot.conversationId}
        runSpecId={selectedRun?.runSpecId ?? null}
        teamEnabled={teamEnabled}
        teamRevision={team.state.kind === "available" ? team.state.data.revision : null}
        teamMutation={teamMutation}
        onNotice={onNotice}
        onAuthorizationLost={loseAuthorization}
        onConversationChanged={onConversationChanged}
        onRefresh={onRefresh}
      />);
  const askRole = (role: ProfessionalRole) => {
    setRecipientRequest((current) => ({ role, key: (current?.key ?? 0) + 1 }));
  };
  const openCitation = (citation: TeamCitation, output: TeamOutput, focusReturn: HTMLElement) => {
    setRoleEvidenceRequest(null);
    setTeamCitationRequest((current) => ({ citation, output, focusReturn, key: (current?.key ?? 0) + 1 }));
  };
  if (teamSurfaceEnabled && selectedRun) {
    const visibleTasks = team.state.kind === "available" ? tasksForRevision(team.state.data, selectedTeamRevision ?? team.state.data.revision) : [];
    const questionsByRole = visibleTasks.map((task) => [
      ...(task.output?.content.checks.filter((check) => check.status === "blocker").map((check) => check.detail) ?? []),
      ...(task.output?.content.gaps ?? []),
    ].map((text, index) => ({ id: `${task.id}/${index}`, role: task.role, text })));
    // Give each role a place in the small sidebar before showing further gaps.
    const questions = Array.from({ length: Math.max(0, ...questionsByRole.map((items) => items.length)) }, (_, index) =>
      questionsByRole.flatMap((items) => items[index] ? [items[index]] : [])).flat();
    const questionTarget = document.getElementById("gateway-key-questions");
    const evidenceRoles: Record<string, string[]> = {};
    for (const task of visibleTasks) for (const evidenceId of task.output?.evidence_ids ?? []) {
      (evidenceRoles[evidenceId] ??= []).push(professionalRoleLabels[task.role]);
    }
    const scope = (<div className="gateway-team-scope" aria-label="研究范围" data-expanded={scopeOpen}>
        <div><strong>研究范围{selectedRun.scope ? "（已固定）" : "（待确认）"}</strong><span>本轮创建于 <time dateTime={selectedRun.createdAt}>{new Date(selectedRun.createdAt).toLocaleDateString("zh-CN")}</time></span></div>
        <div className="gateway-scope-chips">{selectedRun.scope ? <>
          <span className="gateway-scope-topic" title={selectedRun.scope.topic}>主题：{selectedRun.scope.topic}</span>
          {selectedRun.scope.subjectLabel ? <span>对象：{selectedRun.scope.subjectLabel}</span> : null}
          <span>证据截止：{new Date(selectedRun.scope.evidenceCutoffAt).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" })}</span>
          <span>来源：{selectedRun.scope.sourcePolicy.allowedSourceRoles.map((role) => role === "company_disclosure" ? "公司披露" : "授权数据").join("、") || "未列明"}{selectedRun.scope.sourcePolicy.hasIntakeMaterial ? "、本轮用户材料" : ""}</span>
        </> : <><span>范围口径：等待服务端确认</span><span>材料：以本轮获授权来源为准</span></>}</div>
        {scopeOpen ? <div className="gateway-scope-description">
          {selectedRun.scope ? <><p>研究主题：{selectedRun.scope.topic}</p><p>证据截止时间：{new Date(selectedRun.scope.evidenceCutoffAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}（北京时间）</p>{selectedRun.scope.caseEvidenceCutoffDate ? <p>研究记录截止日：{selectedRun.scope.caseEvidenceCutoffDate}</p> : null}</> : <p>当前记录尚无可确认的冻结范围详情。</p>}
          <p>在输入框使用「调整范围：…」会创建新一轮研究，已有任务与产出保留在原轮次中。</p>
        </div> : null}
        <RunSelector runs={runs} selectedRunSpecId={selectedRun.runSpecId} onSelect={(runSpecId) => { setSelectedRunSpecId(runSpecId); setSelectedTeamRevision(undefined); setTeamCitationRequest(null); setRecipientRequest(null); }} />
      </div>);
    return <section className="gateway-conversation gateway-conversation--team" aria-label="研究对话">
      <header className="gateway-team-titlebar">
        <div className="gateway-team-titleline">
          <h1 className={titleExpanded ? "is-expanded" : ""}>{snapshot.title ?? "未命名研究对话"}</h1>
          <span className={`gateway-status gateway-status--${selectedRun.status}`}>{nativeStatusLabels[selectedRun.status]}</span>
          <span className={`gateway-connection gateway-connection--${connection}`} title={connectionLabels[connection]}>● {connection === "live" ? "已连接" : connectionLabels[connection]}</span>
        </div>
        <div className="gateway-team-title-actions">
          <button type="button" aria-expanded={titleExpanded} onClick={() => setTitleExpanded((value) => !value)}>{titleExpanded ? "收起完整请求" : "展开完整请求"}</button>
          <button type="button" aria-expanded={scopeOpen} onClick={() => setScopeOpen((value) => !value)}>研究范围</button>
          <a href="/">＋ 新建研究</a>
        </div>
      </header>

      <IssueNotice issue={issue} onRefresh={onRefresh} />
      <GatewayResearchContent client={client} conversationId={snapshot.conversationId} run={selectedRun} connection={connection}
        authorizedArtifactsKey={authorizedArtifactsKey}
        onSourceAccessInvalidated={team.invalidate}
        refreshSequence={snapshot.events.filter((event) => event.runSpecId === selectedRun.runSpecId && ["evidence_available", "draft_ref", "validation", "role_completed", "role_failed"].includes(event.type)).at(-1)?.sequence ?? 0}
        citationRequest={teamCitationRequest} roleEvidenceRequest={roleEvidenceRequest} evidenceRoles={evidenceRoles} onAuthorizationLost={loseAuthorization}
        renderLayout={({ nativeContent, evidence, evidenceRequestKey }) => <ProfessionalTeamWorkspace
          state={team.state} selectedRevision={selectedTeamRevision} onSelectRevision={setSelectedTeamRevision}
          mutation={teamMutation} onRefresh={team.refresh} nativeStatus={selectedRun.status}
          onOpenCitation={openCitation} onAskRole={askRole}
          onShowRoleEvidence={(task) => { setTeamCitationRequest(null); setRoleEvidenceRequest((current) => ({ key: (current?.key ?? 0) + 1, label: professionalRoleLabels[task.role], ids: task.output?.evidence_ids ?? [] })); }}
          goal={{ text: snapshot.messages[0]?.text ?? snapshot.title ?? "", createdAt: snapshot.messages[0]?.createdAt ?? snapshot.createdAt }}
          scope={scope} composer={composer} composerRequestKey={recipientRequest?.key} evidence={evidence} evidenceRequestKey={evidenceRequestKey}
          nativeContent={<>{nativeContent}<details className="gateway-stage-disclosure"><summary>执行阶段概览</summary><GatewayStageStrip snapshot={snapshot} /></details></>}
          messageHistory={messageHistory} />} />
      {questionTarget ? createPortal(<section className="gateway-key-questions" aria-label="关键问题">
        <h3>关键问题（{questions.length}）</h3>
        {questions.length ? questions.slice(0, 5).map((question) => <button type="button" key={question.id} onClick={() => askRole(question.role)}>
          <span>{question.text}</span><small>由 {professionalRoleLabels[question.role]} 跟进 ↗</small>
        </button>) : <p>角色任务产出后，在这里跟进证据缺口与阻塞项。</p>}
        {questions.length > 5 ? <p>其余 {questions.length - 5} 项见右侧「待解决问题」。</p> : null}
      </section>, questionTarget) : null}
    </section>;
  }

  return (
    <section className="gateway-conversation" aria-label="研究对话">
      <header className="research-header gateway-conversation__header">
        <div className="research-title">
          <div>
            <p className="gateway-eyebrow">FundClaw · 私有研究对话</p>
            <h1 className={titleExpanded ? "research-title-expanded" : "research-title-collapsed"}>{snapshot.title ?? "未命名研究对话"}</h1>
            {snapshot.title ? <button type="button" aria-expanded={titleExpanded} onClick={() => setTitleExpanded((value) => !value)}>{titleExpanded ? "收起完整请求" : "展开完整请求"}</button> : null}
            {selectedRun ? <p className="gateway-run-overview">{nativeStatusLabels[selectedRun.status]} · {({ queued: "等待调度", planning: "规划研究", retrieve: "获取来源", analyze: "分析材料", assessing: "评估判断", conclude: "形成报告", complete: "执行结束，仍需核对缺口", failed: "执行失败", cancelled: "已停止", unknown: "阶段未确认" } as const)[selectedRun.execution?.stage ?? "unknown"]}</p> : null}
            {selectedRun?.execution ? <p className="gateway-run-overview">最近任务进展 <time dateTime={selectedRun.execution.updatedAt}>{formatTime(selectedRun.execution.updatedAt)}</time>{connection !== "live" ? " · 当前为最后收到的状态" : ""}</p> : null}
          </div>
          <span className={`gateway-connection gateway-connection--${connection}`}>
            ● {connectionLabels[connection]}
          </span>
        </div>
      </header>

      {selectedRun ? <>
        <RunSelector runs={runs} selectedRunSpecId={selectedRun.runSpecId} onSelect={(runSpecId) => { setSelectedRunSpecId(runSpecId); setSelectedTeamRevision(undefined); setTeamCitationRequest(null); }} />
        {teamSurfaceEnabled ? <ProfessionalRoleFrame state={team.state} selectedRevision={selectedTeamRevision} onSelectRevision={setSelectedTeamRevision}
          mutation={teamMutation} onRefresh={team.refresh} nativeStatus={selectedRun.status}
          onOpenCitation={(citation, output, focusReturn) => setTeamCitationRequest((current) => ({ citation, output, focusReturn, key: (current?.key ?? 0) + 1 }))} />
          : <ProfessionalRoleFrame />}
        <GatewayResearchContent client={client} conversationId={snapshot.conversationId} run={selectedRun} connection={connection}
          authorizedArtifactsKey={authorizedArtifactsKey}
          onSourceAccessInvalidated={team.invalidate}
          refreshSequence={snapshot.events.filter((event) => event.runSpecId === selectedRun.runSpecId && ["evidence_available", "draft_ref", "validation", "role_completed", "role_failed"].includes(event.type)).at(-1)?.sequence ?? 0}
          citationRequest={teamCitationRequest}
          onAuthorizationLost={loseAuthorization} />
        <details className="gateway-stage-disclosure"><summary>执行阶段概览</summary><GatewayStageStrip snapshot={snapshot} /></details>
      </> : <><ProfessionalRoleFrame /><GatewayStageStrip snapshot={snapshot} /></>}
      <IssueNotice issue={issue} onRefresh={onRefresh} />

      {messageHistory}

      {composer}

    </section>
  );
}

function GatewayStageStrip({
  snapshot,
}: {
  snapshot: GatewayConversationSnapshot;
}): ReactElement {
  return (
    <section className="gateway-stage-strip" aria-labelledby="gateway-stage-heading">
      <header className="gateway-section-heading">
        <div>
          <h2 id="gateway-stage-heading">真实 Gateway 执行阶段</h2>
          <p>阶段来自当前 Gateway 投影，不映射为产业、财务、策略或质控角色任务。</p>
        </div>
      </header>
      {snapshot.roles.length === 0 ? (
        <p className="gateway-empty-copy">尚未报告可安全展示的执行阶段。</p>
      ) : (
        <ol className="gateway-stage-list">
          {[...snapshot.roles]
            .sort((left, right) => left.latestSequence - right.latestSequence)
            .map((role) => (
              <li key={`${role.runSpecId}-${role.role}`}>
                <strong>{roleLabels[role.role]}</strong>
                <span className={`gateway-status gateway-status--${role.status}`}>
                  {roleStatusLabels[role.status]}
                </span>
              </li>
            ))}
        </ol>
      )}
      {snapshot.runs.length > 0 ? (
        <div className="gateway-native-status" aria-label="原生运行状态">
          <strong>原生运行状态（以快照为准）</strong>
          {snapshot.runs.map((run) => (
            <span key={run.runSpecId}>{nativeStatusLabels[run.status]}</span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function RunSelector({ runs, selectedRunSpecId, onSelect }: { runs: GatewayConversationSnapshot["runs"]; selectedRunSpecId: string; onSelect: (runSpecId: string) => void }): ReactElement | null {
  if (runs.length <= 1) return null;
  return <div className="gateway-run-selector" aria-label="研究轮次">
    <label>研究轮次
      <select value={selectedRunSpecId} onChange={(event) => onSelect(event.target.value)}>
        {runs.map((run, index) => <option key={run.runSpecId} value={run.runSpecId}>第 {index + 1} 轮 · {nativeStatusLabels[run.status]}</option>)}
      </select>
    </label>
  </div>;
}

function GatewayMessageComposer({
  client,
  conversationId,
  runSpecId,
  teamRevision,
  teamEnabled,
  teamMutation,
  recipientRequest,
  readOnly = false,
  teamStale = false,
  onNotice,
  onAuthorizationLost,
  onConversationChanged,
  onRefresh,
}: {
  client: GatewayConversationClient;
  conversationId: string;
  runSpecId: string | null;
  teamRevision: number | null;
  teamEnabled: boolean;
  teamMutation: Pick<ReturnType<typeof useTeamMutation>, "busy" | "submit">;
  recipientRequest?: { role: ProfessionalRole; key: number } | null;
  readOnly?: boolean;
  teamStale?: boolean;
  onNotice?: (notice: string) => void;
  onAuthorizationLost: () => void;
  onConversationChanged: () => void;
  onRefresh: () => void;
}): ReactElement {
  const controllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState("");
  const [recipient, setRecipient] = useState<"team" | ProfessionalRole>("team");
  const [pending, setPending] = useState<PendingMessage | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => () => controllerRef.current?.abort(), []);
  useEffect(() => {
    if (!recipientRequest) return;
    setRecipient(recipientRequest.role);
    inputRef.current?.focus({ preventScroll: true });
  }, [recipientRequest]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const text = draft.trim();
    if (teamStale) { setNotice("团队状态待更新，请重新读取成功后再提交。"); return; }
    if (readOnly) { setNotice("历史版本只读，请切换到当前版本后继续研究。"); return; }
    if (!text || isSubmitting || teamMutation.busy) return;
    if (!isScopeChange(text) && text.length > 20_000) {
      setNotice("专业团队指令最多 20,000 字；当前内容已保留，请删减后提交。");
      return;
    }
    if (!isScopeChange(text) && teamEnabled) {
      if (!runSpecId || teamRevision === null) {
        setNotice("专业团队版本尚未读取完成，请稍后重试。");
        return;
      }
      setNotice("");
      const ok = await teamMutation.submit({ kind: "message", body: { text, recipient, expected_revision: teamRevision } });
      if (ok) setDraft("");
      return;
    }
    const request = pending?.text === text
      ? pending
      : { text, idempotencyKey: newIdempotencyKey() };
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setPending(request);
    setIsSubmitting(true);
    setNotice("");

    try {
      const receipt = await client.sendMessage(conversationId, {
        text: request.text,
        idempotencyKey: request.idempotencyKey,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (isRejectedIntent(receipt)) {
        const safeNotice = "该请求暂不受 Gateway P0 支持，未启动新的研究任务。";
        setNotice(safeNotice);
        onNotice?.(safeNotice);
        setPending(null);
        onConversationChanged();
        onRefresh();
        return;
      }
      setDraft("");
      setPending(null);
      onConversationChanged();
      onRefresh();
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (
          cause instanceof GatewayHttpError &&
          (cause.status === 401 || cause.status === 403)
        ) {
          onAuthorizationLost();
          return;
        }
        setNotice("无法确认本次提交是否已送达。请使用相同请求重试。");
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsSubmitting(false);
      }
    }
  }

  return (
    <form className="composer gateway-composer" onSubmit={submit}>
      <label className="visually-hidden" htmlFor="gateway-followup-message">继续研究</label>
      <textarea
        ref={inputRef}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); event.currentTarget.form?.requestSubmit();
          }
        }}
        aria-describedby="gateway-composer-helper"
        id="gateway-followup-message"
        onChange={(event) => setDraft(event.target.value)}
        placeholder={recipient === "team" ? "向研究团队发送消息，或提出研究请求…" : `向${professionalRoleLabels[recipient]}补充要求…`}
        value={draft}
      />
      <div>
        <p id="gateway-composer-helper">
          {teamStale ? "团队状态待更新，草稿已保留；重新读取成功后可提交。" : readOnly ? "历史版本只读，请切换当前版本后继续研究。" : "Enter 发送 · Shift + Enter 换行"}
        </p>
        <label className="gateway-recipient-select"><span>专业团队收件人</span>
          <select value={recipient} onChange={(event) => setRecipient(event.target.value as typeof recipient)} disabled={teamStale || readOnly || isSubmitting || teamMutation.busy}>
            <option value="team">全体团队</option>
            {professionalRoles.map((role) => <option value={role} key={role}>{professionalRoleLabels[role]}</option>)}
          </select>
        </label>
        <button className="composer-send" disabled={teamStale || readOnly || !draft.trim() || isSubmitting || teamMutation.busy} type="submit">
          {isSubmitting || teamMutation.busy ? "发送中…" : "提交"}
        </button>
      </div>
      {notice ? <p className="gateway-inline-notice" role="alert">{notice}</p> : null}
    </form>
  );
}

function isScopeChange(text: string): boolean {
  return /^调整范围[:：]/.test(text.trim());
}

function IssueNotice({
  issue,
  onRefresh,
}: {
  issue: GatewayConversationIssue;
  onRefresh: () => void;
}): ReactElement | null {
  if (issue === "none" || issue === "access_revoked") return null;
  const message = issue === "auth_required"
    ? "研究会话需要重新验证。"
    : issue === "projection_unavailable"
      ? "执行投影暂不可用；当前未显示推测性状态。"
      : "暂时无法读取 Gateway 状态；本次请求结果未知。";
  return (
    <aside className="gateway-issue" role="alert">
      <p>{message}</p>
      {issue !== "auth_required" ? <button type="button" onClick={onRefresh}>重新连接</button> : null}
    </aside>
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
