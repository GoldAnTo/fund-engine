import { useEffect, useRef, useState, type KeyboardEvent, type ReactElement, type ReactNode, type RefObject } from "react";

import type { GatewayNativeRunStatus } from "@/gateway/contracts";
import {
  professionalRoleLabels,
  professionalRoles,
  tasksForRevision,
  type GatewayTeam,
  type ProfessionalRole,
  type TeamCitation,
  type TeamCheck,
  type TeamOutput,
  type TeamTask,
} from "@/gateway/team";
import type { TeamMutation, TeamMutationController } from "@/gateway/useTeamMutation";
import type { TeamReadState } from "@/gateway/useGatewayTeam";

import "./ProfessionalTeamWorkspace.css";

type RoleFilter = "all" | ProfessionalRole;
type CenterTab = "dialogue" | "timeline" | "outputs";
type RightTab = "evidence" | "problems" | "review";
type MobilePane = "work" | "evidence";

export type ProfessionalTeamWorkspaceProps = {
  state?: TeamReadState;
  selectedRevision?: number;
  onSelectRevision?: (revision: number) => void;
  mutation?: Pick<TeamMutationController, "busy" | "notice" | "unknown" | "submit" | "retry">;
  onOpenCitation?: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void;
  onRefresh?: () => void;
  nativeStatus?: GatewayNativeRunStatus;
  onAskRole?: (role: ProfessionalRole) => void;
  onShowRoleEvidence?: (task: TeamTask) => void;
  goal?: { text: string; createdAt: string };
  scope?: ReactNode;
  composer: ReactNode;
  evidence: ReactNode;
  nativeContent: ReactNode;
  messageHistory: ReactNode;
  evidenceRequestKey?: number;
  composerRequestKey?: number;
};

const roleMarkers: Record<ProfessionalRole, string> = { industry: "产", finance: "财", strategy: "策", quality: "审" };
const roleShortLabels: Record<ProfessionalRole, string> = { industry: "产业", finance: "财务", strategy: "策略", quality: "质控" };
const taskStatusLabels: Record<TeamTask["status"], string> = {
  queued: "等待执行",
  running: "执行中",
  blocked: "已阻塞",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};
const teamStatusLabels: Record<GatewayTeam["status"], string> = {
  not_started: "未启动",
  active: "运行中",
  paused: "已暂停",
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
const basisLabels = { supported: "支持", contradicted: "反驳", uncertain: "不确定" } as const;
const checkKindLabels: Record<TeamCheck["kind"], string> = {
  citations: "引用",
  periods: "期间",
  units: "口径",
  source_independence: "来源独立性",
  counter_evidence: "反证",
  completeness: "完整性",
};
const checkStatusLabels: Record<TeamCheck["status"], string> = { pass: "通过", warning: "提示", blocker: "阻塞" };
const reviewDecisionLabels = { approved: "通过", changes_requested: "要求修改" } as const;
const reasonGuidance: Record<string, { title: string; next: string }> = {
  invalid_model_result: { title: "校验失败", next: "模型输出未通过结构或证据校验，请检查引用、期间和输出格式后重试。" },
  validation_failed: { title: "校验失败", next: "质控检查没有通过，请查看阻塞项后重试或补充指令。" },
  dependency_failed: { title: "上游未完成", next: "依赖角色尚未产出可用结果，请先查看前序任务状态。" },
  dependency_unavailable: { title: "上游未完成", next: "依赖产出暂不可用，请等上游角色完成或重试依赖任务。" },
  dependency_pending: { title: "上游未完成", next: "依赖任务仍在执行队列中，请等待团队刷新。" },
  waiting_for_native_completion: { title: "等待资料采集", next: "等待资料采集与原生研究完成；专业团队不会提前使用部分证据完成四角色流程。" },
  evidence_gap: { title: "缺证", next: "当前结论缺少足够证据，请补充来源范围或要求角色继续检索。" },
  insufficient_evidence: { title: "缺证", next: "证据不足以支持输出，请补充材料或缩小研究问题。" },
  source_authorization_changed: { title: "来源授权变化", next: "证据访问范围已变化，请重新读取来源并核对授权状态。" },
  authorization_changed: { title: "来源授权变化", next: "访问权限已变化，请重新确认身份后再继续。" },
  budget_exceeded: { title: "预算超限", next: "本次模型预算已用尽，请缩小指令或提高后端预算后重试。" },
  token_budget_exceeded: { title: "预算超限", next: "Token 预算不足，请缩短输入或拆分任务。" },
};

export function ProfessionalTeamWorkspace({
  state,
  selectedRevision,
  onSelectRevision,
  mutation,
  onOpenCitation,
  onRefresh,
  nativeStatus,
  onAskRole,
  onShowRoleEvidence,
  goal,
  scope,
  composer,
  evidence,
  nativeContent,
  messageHistory,
  evidenceRequestKey,
  composerRequestKey,
}: ProfessionalTeamWorkspaceProps): ReactElement {
  const [centerTab, setCenterTab] = useState<CenterTab>("dialogue");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [expanded, setExpanded] = useState<Set<ProfessionalRole>>(new Set());
  const [rightTab, setRightTab] = useState<RightTab>("evidence");
  const [mobilePane, setMobilePane] = useState<MobilePane>("work");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [decision, setDecision] = useState<"approved" | "changes_requested">("approved");
  const [comment, setComment] = useState("");
  const evidencePanel = useRef<HTMLDivElement | null>(null);
  const previousEvidenceRequestKey = useRef<number | undefined>(evidenceRequestKey);
  const previousComposerRequestKey = useRef<number | undefined>(composerRequestKey);

  useEffect(() => {
    if (evidenceRequestKey === undefined) return;
    const previous = previousEvidenceRequestKey.current;
    previousEvidenceRequestKey.current = evidenceRequestKey;
    if (evidenceRequestKey <= 0 && previous === undefined) return;
    if (previous !== undefined && evidenceRequestKey === previous) return;
    setRightTab("evidence");
    setMobilePane("evidence");
    window.setTimeout(() => focusEvidencePanel(evidencePanel.current), 0);
  }, [evidenceRequestKey]);

  useEffect(() => {
    if (composerRequestKey === undefined) return;
    const previous = previousComposerRequestKey.current;
    previousComposerRequestKey.current = composerRequestKey;
    if (composerRequestKey <= 0 && previous === undefined) return;
    if (previous !== undefined && composerRequestKey === previous) return;
    setMobilePane("work");
    window.setTimeout(focusComposer, 0);
  }, [composerRequestKey]);

  if (!state || state.kind !== "available") {
    const center = <UnavailableCenter state={state} scope={scope} messageHistory={messageHistory}
      nativeContent={nativeContent} roleFilter={roleFilter} setRoleFilter={setRoleFilter}
      centerTab={centerTab} setCenterTab={setCenterTab} expanded={expanded} setExpanded={setExpanded}
      onRefresh={onRefresh} onAskRole={(role) => { setMobilePane("work"); onAskRole?.(role); window.setTimeout(focusComposer, 0); }} />;
    return <WorkspaceShell composer={composer} evidence={evidence} evidencePanel={evidencePanel} mobilePane={mobilePane} rightTab={rightTab}
      setMobilePane={setMobilePane} setRightTab={setRightTab}
      center={center}
      problems={<EmptySidePanel title="团队问题尚未读取" copy="接口读取完成后，这里会显示真实缺口、阻塞检查与原因代码。" />}
      review={<EmptySidePanel title="人工复核尚不可用" copy="专业团队版本读取完成后，才能提交或查看绑定到四个角色产出的复核记录。" />} />;
  }

  const team = state.data;
  const visibleRevision = clampRevision(selectedRevision, team.revision);
  const visibleTasks = tasksForRevision(team, visibleRevision);
  const taskByRole = new Map(visibleTasks.map((task) => [task.role, task]));
  const allTaskById = new Map(team.tasks.map((task) => [task.id, task]));
  const filteredRoles = professionalRoles.filter((role) => roleFilter === "all" || roleFilter === role);
  const selectedTaskCandidate = selectedTaskId ? allTaskById.get(selectedTaskId) ?? null : null;
  const selectedTask = selectedTaskCandidate && selectedTaskCandidate.revision <= visibleRevision ? selectedTaskCandidate : null;
  const outputIds = professionalRoles.map((role) => taskByRole.get(role)?.output?.id).filter((id): id is string => Boolean(id));
  const historical = visibleRevision !== team.revision;
  const stale = Boolean(state.stale);
  const readOnly = historical || stale;
  const pending = Boolean(mutation?.busy);
  const canMutate = Boolean(mutation) && !readOnly && !pending;
  const canReview = canMutate && outputIds.length === 4 && comment.trim().length > 0;
  const problems = deriveProblems(team, visibleRevision, taskByRole, nativeStatus);

  function submit(operation: TeamMutation): void {
    if (!canMutate) return;
    void mutation?.submit(operation);
  }
  function command(kind: "start" | "pause" | "resume" | "cancel"): void {
    submit({ kind: "command", body: { kind, expected_revision: team.revision } });
  }
  function retryTask(task: TeamTask): void {
    submit({ kind: "command", body: { kind: "retry", task_id: task.id, expected_revision: team.revision } });
  }
  function review(): void {
    if (!canReview) return;
    submit({ kind: "review", body: { expected_revision: team.revision, output_ids: outputIds, decision, comment: comment.trim() } });
  }
  function openCitation(citation: TeamCitation, output: TeamOutput, trigger: HTMLElement): void {
    setRightTab("evidence");
    setMobilePane("evidence");
    onOpenCitation?.(citation, output, trigger);
    window.setTimeout(() => focusEvidencePanel(evidencePanel.current), 0);
  }
  function askRole(role: ProfessionalRole): void {
    setMobilePane("work");
    onAskRole?.(role);
    window.setTimeout(focusComposer, 0);
  }
  function inspectTask(task: TeamTask): void {
    setSelectedTaskId(task.id);
    setRoleFilter(task.role);
    setCenterTab("outputs");
    setExpanded((current) => new Set([...current, task.role]));
  }
  function showRoleEvidence(task: TeamTask): void {
    setRightTab("evidence");
    setMobilePane("evidence");
    onShowRoleEvidence?.(task);
    window.setTimeout(() => focusEvidencePanel(evidencePanel.current), 0);
  }

  const center = <section className="team-workspace__center" aria-label="多角色研究工作台">
    {scope ? <div className="team-workspace__scope">{scope}</div> : null}
    <WorkspaceToolbar team={team} visibleRevision={visibleRevision} visibleTasks={visibleTasks}
      onSelectRevision={onSelectRevision} nativeStatus={nativeStatus} refreshing={state.refreshing}
      readOnly={historical} stale={stale} canMutate={canMutate} onCommand={command} onRefresh={onRefresh} />
    {stale ? <p className="team-workspace__notice" role="status">团队状态待更新，当前显示上次成功读取的内容；恢复读取前暂停提交。可重新读取团队以重试。</p> : null}
    {mutation?.notice ? <p className="team-workspace__notice" role="status">{mutation.notice}{mutation.unknown ? <button type="button" onClick={() => { if (!stale && !pending) void mutation.retry(); }} disabled={pending || stale}>重试原请求</button> : null}</p> : null}
    {historical ? <p className="team-workspace__history-note">正在查看历史版本 {visibleRevision}，控制、重试与人工复核为只读。</p> : null}
    <div className="team-workspace__tabs" role="tablist" aria-label="团队工作区视图">
      <TabButton active={centerTab === "dialogue"} onClick={() => setCenterTab("dialogue")}>研究对话</TabButton>
      <TabButton active={centerTab === "timeline"} onClick={() => setCenterTab("timeline")}>协作时间线</TabButton>
      <TabButton active={centerTab === "outputs"} onClick={() => setCenterTab("outputs")}>研究产出</TabButton>
    </div>
    <div className="team-workspace__filters" aria-label="角色筛选">
      <RoleFilterButton value="all" roleFilter={roleFilter} setRoleFilter={setRoleFilter}>全部角色</RoleFilterButton>
      {professionalRoles.map((role) => <RoleFilterButton key={role} value={role} roleFilter={roleFilter} setRoleFilter={setRoleFilter}>{roleShortLabels[role]}</RoleFilterButton>)}
      <button className="team-workspace__expand-all" type="button" onClick={() => setExpanded(expanded.size === professionalRoles.length ? new Set() : new Set(professionalRoles))}>
        {expanded.size === professionalRoles.length ? "收起工作记录" : "展开工作记录"}
      </button>
    </div>
    <div className="team-workspace__feed" role="tabpanel">
      {goal ? <GoalRow goal={goal} /> : null}
      {centerTab === "dialogue" ? <>
        {filteredRoles.map((role) => <RoleRow key={role} role={role} task={taskByRole.get(role) ?? null} taskById={allTaskById}
          expanded={expanded.has(role)} readOnly={readOnly} pending={pending} canMutate={Boolean(mutation)}
          onToggle={() => setExpanded(toggleExpanded(expanded, role))} onAskRole={askRole} onRetry={retryTask}
          onOpenCitation={openCitation} onInspectTask={inspectTask} onShowRoleEvidence={showRoleEvidence} />)}
      </> : null}
      {centerTab === "timeline" ? <Timeline roles={filteredRoles} taskByRole={taskByRole} messageHistory={messageHistory}
        nativeContent={nativeContent} onInspectTask={inspectTask} /> : null}
      {centerTab === "outputs" ? <Outputs roles={filteredRoles} taskByRole={taskByRole} selectedTask={selectedTask}
        taskById={allTaskById} nativeContent={nativeContent} onOpenCitation={openCitation} onInspectTask={inspectTask} /> : null}
    </div>
  </section>;

  return <WorkspaceShell composer={composer} evidence={evidence} evidencePanel={evidencePanel} mobilePane={mobilePane} rightTab={rightTab}
    setMobilePane={setMobilePane} setRightTab={setRightTab}
    center={center}
    problems={<ProblemPanel problems={problems} onAskRole={askRole} onInspectTask={inspectTask} />}
    review={<ReviewPanel team={team} visibleRevision={visibleRevision} outputIds={outputIds} readOnly={readOnly} stale={stale} pending={pending}
      canReview={canReview} decision={decision} setDecision={setDecision} comment={comment} setComment={setComment} onReview={review} />} />;
}

function WorkspaceShell({ center, composer, evidence, evidencePanel, problems, review, mobilePane, rightTab, setMobilePane, setRightTab }: {
  center: ReactNode;
  composer: ReactNode;
  evidence: ReactNode;
  evidencePanel: RefObject<HTMLDivElement>;
  problems: ReactNode;
  review: ReactNode;
  mobilePane: MobilePane;
  rightTab: RightTab;
  setMobilePane: (pane: MobilePane) => void;
  setRightTab: (tab: RightTab) => void;
}): ReactElement {
  return <section className="team-workspace" data-mobile-pane={mobilePane}>
    <div className="team-workspace__mobile-switch" aria-label="工作区面板">
      <button type="button" aria-pressed={mobilePane === "work"} onClick={() => setMobilePane("work")}>团队工作区</button>
      <button type="button" aria-pressed={mobilePane === "evidence"} onClick={() => setMobilePane("evidence")}>证据与复核</button>
    </div>
    <div className="team-workspace__main-pane"><div className="team-workspace__center-slot">{center}</div><div className="team-workspace__composer">{composer}</div></div>
    <aside className="team-workspace__right" aria-label="证据、问题与复核">
      <div className="team-workspace__right-tabs" role="tablist" aria-label="右侧面板">
        <RightTabButton tab="evidence" active={rightTab === "evidence"} setRightTab={setRightTab}>证据与来源</RightTabButton>
        <RightTabButton tab="problems" active={rightTab === "problems"} setRightTab={setRightTab}>待解决问题</RightTabButton>
        <RightTabButton tab="review" active={rightTab === "review"} setRightTab={setRightTab}>审查状态</RightTabButton>
      </div>
      <div className="team-workspace__right-body">
        <div className="team-workspace__right-panel" hidden={rightTab !== "evidence"} ref={evidencePanel}>{evidence}</div>
        <div className="team-workspace__right-panel" hidden={rightTab !== "problems"}>{problems}</div>
        <div className="team-workspace__right-panel" hidden={rightTab !== "review"}>{review}</div>
      </div>
    </aside>
  </section>;
}

function WorkspaceToolbar({ team, visibleRevision, visibleTasks, onSelectRevision, nativeStatus, refreshing, readOnly, stale, canMutate, onCommand, onRefresh }: {
  team: GatewayTeam;
  visibleRevision: number;
  visibleTasks: TeamTask[];
  onSelectRevision?: (revision: number) => void;
  nativeStatus?: GatewayNativeRunStatus;
  refreshing: boolean;
  readOnly: boolean;
  stale: boolean;
  canMutate: boolean;
  onCommand: (kind: "start" | "pause" | "resume" | "cancel") => void;
  onRefresh?: () => void;
}): ReactElement {
  const revisions = team.revision > 0 ? Array.from({ length: team.revision }, (_, index) => index + 1) : [0];
  return <header className="team-workspace__toolbar">
    <div>
      <p>{stale ? "上次读取：" : ""}{readOnly ? `查看版本 ${visibleRevision} · 最新版本 ${team.revision}` : `团队 ${teamProgressLabel(team, visibleTasks)} · 版本 ${visibleRevision}`}{refreshing ? " · 刷新中" : ""} · AI 草案待人工复核</p>
    </div>
    <div className="team-workspace__controls" aria-label="专业团队控制">
      <label>团队版本
        <select value={visibleRevision} onChange={(event) => onSelectRevision?.(Number(event.target.value))} disabled={team.revision === 0 || !onSelectRevision}>
          {revisions.map((revision) => <option key={revision} value={revision}>{revision === 0 ? "未启动" : `版本 ${revision}`}</option>)}
        </select>
      </label>
      {team.status === "not_started" ? <button type="button" disabled={!canMutate} onClick={() => onCommand("start")}>启动专业团队</button> : null}
      {team.status === "active" ? <button type="button" disabled={!canMutate} onClick={() => onCommand("pause")}>暂停</button> : null}
      {team.status === "paused" ? <button type="button" disabled={!canMutate} onClick={() => onCommand("resume")}>恢复</button> : null}
      {team.status === "active" || team.status === "paused" ? <button type="button" disabled={!canMutate} onClick={() => onCommand("cancel")}>取消</button> : null}
      <details className="team-workspace__toolbar-detail"><summary>执行详情</summary><p>Gateway {nativeStatus ? nativeStatusLabels[nativeStatus] : "未提供"}</p></details>
      <button type="button" onClick={onRefresh}>重新读取团队</button>
      {readOnly ? <span>历史版本只读</span> : null}
    </div>
  </header>;
}

function UnavailableCenter({ state, scope, messageHistory, nativeContent, roleFilter, setRoleFilter, centerTab, setCenterTab, expanded, setExpanded, onRefresh, onAskRole }: {
  state?: TeamReadState;
  scope?: ReactNode;
  messageHistory: ReactNode;
  nativeContent: ReactNode;
  roleFilter: RoleFilter;
  setRoleFilter: (value: RoleFilter) => void;
  centerTab: CenterTab;
  setCenterTab: (value: CenterTab) => void;
  expanded: Set<ProfessionalRole>;
  setExpanded: (value: Set<ProfessionalRole>) => void;
  onRefresh?: () => void;
  onAskRole?: (role: ProfessionalRole) => void;
}): ReactElement {
  const filteredRoles = professionalRoles.filter((role) => roleFilter === "all" || roleFilter === role);
  const statusUnknown = Boolean(state && state.kind !== "unsupported");
  return <section className="team-workspace__center" aria-label="多角色研究工作台">
    {scope ? <div className="team-workspace__scope">{scope}</div> : null}
    <header className="team-workspace__toolbar">
      <div><p>{readStateMessage(state)}</p><p>{statusUnknown ? "角色任务状态尚未确认，恢复读取后显示实际任务、产出与证据。" : "四个专业角色会在真实团队启动后写入任务、产出、证据与尝试记录。"}</p></div>
      <div className="team-workspace__controls" aria-label="专业团队控制">
        {state?.kind !== "unsupported" ? <button type="button" onClick={onRefresh}>重新读取团队</button> : null}
      </div>
    </header>
    <div className="team-workspace__tabs" role="tablist" aria-label="团队工作区视图">
      <TabButton active={centerTab === "dialogue"} onClick={() => setCenterTab("dialogue")}>研究对话</TabButton>
      <TabButton active={centerTab === "timeline"} onClick={() => setCenterTab("timeline")}>协作时间线</TabButton>
      <TabButton active={centerTab === "outputs"} onClick={() => setCenterTab("outputs")}>研究产出</TabButton>
    </div>
    <div className="team-workspace__filters" aria-label="角色筛选">
      <RoleFilterButton value="all" roleFilter={roleFilter} setRoleFilter={setRoleFilter}>全部角色</RoleFilterButton>
      {professionalRoles.map((role) => <RoleFilterButton key={role} value={role} roleFilter={roleFilter} setRoleFilter={setRoleFilter}>{roleShortLabels[role]}</RoleFilterButton>)}
      <button className="team-workspace__expand-all" type="button" onClick={() => setExpanded(expanded.size === professionalRoles.length ? new Set() : new Set(professionalRoles))}>
        {expanded.size === professionalRoles.length ? "收起工作记录" : "展开工作记录"}
      </button>
    </div>
    <div className="team-workspace__feed" role="tabpanel">
      {centerTab === "dialogue" ? filteredRoles.map((role) => <RoleRow key={role} role={role} task={null} taskById={new Map()}
        expanded={expanded.has(role)} readOnly pending={false} canMutate={false} statusUnknown={statusUnknown}
        onToggle={() => setExpanded(toggleExpanded(expanded, role))} onAskRole={onAskRole} onRetry={() => undefined}
        onOpenCitation={() => undefined} onInspectTask={() => undefined} />) : null}
      {centerTab === "timeline" ? <div className="team-workspace__timeline">
        <p className="team-workspace__empty">{readStateTimelineMessage(state)}</p>
        <details className="team-workspace__embedded"><summary>已确认的研究请求</summary>{messageHistory}</details>
        <details className="team-workspace__embedded"><summary>原生 Gateway 执行内容</summary>{nativeContent}</details>
      </div> : null}
      {centerTab === "outputs" ? <div className="team-workspace__outputs">
        <p className="team-workspace__empty">专业角色产出尚未读取。这里不会显示样例文件或虚构结果。</p>
        <details className="team-workspace__embedded"><summary>原生 Gateway 内容</summary>{nativeContent}</details>
      </div> : null}
    </div>
  </section>;
}

function RoleRow({ role, task, taskById, expanded, readOnly, pending, canMutate, statusUnknown = false, onToggle, onAskRole, onRetry, onOpenCitation, onInspectTask, onShowRoleEvidence }: {
  role: ProfessionalRole;
  task: TeamTask | null;
  taskById: Map<string, TeamTask>;
  expanded: boolean;
  readOnly: boolean;
  pending: boolean;
  canMutate: boolean;
  statusUnknown?: boolean;
  onToggle: () => void;
  onAskRole?: (role: ProfessionalRole) => void;
  onRetry: (task: TeamTask) => void;
  onOpenCitation: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void;
  onInspectTask: (task: TeamTask) => void;
  onShowRoleEvidence?: (task: TeamTask) => void;
}): ReactElement {
  const output = task?.output_state === "available" ? task.output : null;
  const issue = task ? taskIssue(task) : statusUnknown ? { label: "待更新", text: "请重新读取团队以确认本角色的实际任务状态。" } : { label: "下一步", text: "等待 Gateway 创建本角色任务。" };
  return <article className={`team-workspace__role-row team-workspace__role-row--${role}`} aria-label={`${professionalRoleLabels[role]}工作区`}>
    <time className="team-workspace__time" dateTime={task?.updated_at}>{task ? formatTime(task.updated_at) : statusUnknown ? "待恢复读取" : "未开始"}</time>
    <div className="team-workspace__role-card">
      <div className="team-workspace__identity">
        <span className="team-workspace__avatar" aria-hidden="true">{roleMarkers[role]}</span>
        <div>
          <h3>{professionalRoleLabels[role]}</h3>
          <span className={`team-workspace__badge team-workspace__badge--${task?.status ?? "queued"}`}>{task ? taskStatusLabels[task.status] : statusUnknown ? "状态暂不可用" : "无任务"}</span>
          <small>{role === "quality" ? "AI 质控 · 非人工审核" : "专业研究角色"}</small>
        </div>
      </div>
      <div className="team-workspace__role-body">
        <div className="team-workspace__role-head">
          <p>{output?.content.summary ?? task?.instruction ?? (statusUnknown ? "尚未读取本角色状态，请恢复读取后查看任务与产出。" : "本角色尚未收到已保存指令。")}</p>
          <button type="button" aria-expanded={expanded} aria-label={`${expanded ? "收起" : "展开"}${professionalRoleLabels[role]}工作记录`} onClick={onToggle}>{expanded ? "⌃" : "⌄"}</button>
        </div>
        <div className="team-workspace__task-line">
          <span>{issue.label}</span>
          <p>{issue.text}</p>
        </div>
        <div className="team-workspace__chips">
          {output && task ? <button type="button" onClick={() => onInspectTask(task)}>{draftLabel(role)}</button> : <span>{task?.output_state === "withheld" ? "产出已隐藏" : statusUnknown ? "产出待读取" : "暂无产出"}</span>}
          {task ? <button type="button" disabled={!output || !onShowRoleEvidence} onClick={() => onShowRoleEvidence?.(task)}>关联 {output?.evidence_ids.length ?? 0} 份证据 ↗</button> : <span>{statusUnknown ? "证据待读取" : "关联 0 份证据"}</span>}
          {task ? <span>尝试 {task.attempts.length}</span> : null}
          <button type="button" onClick={() => onAskRole?.(role)}>向此角色补充要求</button>
        </div>
        {expanded ? <div className="team-workspace__details">
          {task ? <TaskFullDetail task={task} taskById={taskById} readOnly={readOnly} pending={pending} canMutate={canMutate}
            onRetry={onRetry} onOpenCitation={onOpenCitation} onInspectTask={onInspectTask} /> : <p className="team-workspace__empty">{statusUnknown ? "本角色任务状态暂不可用，请恢复读取后查看。" : "当前版本没有本角色任务。"}</p>}
        </div> : null}
      </div>
    </div>
  </article>;
}

function TaskFullDetail({ task, taskById, readOnly, pending, canMutate, onRetry, onOpenCitation, onInspectTask }: {
  task: TeamTask;
  taskById: Map<string, TeamTask>;
  readOnly: boolean;
  pending: boolean;
  canMutate: boolean;
  onRetry: (task: TeamTask) => void;
  onOpenCitation: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void;
  onInspectTask: (task: TeamTask) => void;
}): ReactElement {
  const output = task.output_state === "available" ? task.output : null;
  return <div className="team-workspace__detail-grid">
    <dl>
      <dt>任务</dt><dd>{task.instruction || "未提供指令。"}</dd>
      <dt>任务 ID</dt><dd>{task.id}</dd>
      <dt>依赖</dt><dd>{task.dependency_ids.length ? <DependencyLinks task={task} taskById={taskById} onInspectTask={onInspectTask} /> : "无前序角色依赖。"}</dd>
      <dt>原因</dt><dd>{task.reason_code ? `${reasonTitle(task.reason_code)}：${reasonNext(task.reason_code)}` : "未声明阻塞原因。"}</dd>
    </dl>
    {task.output_state === "withheld" ? <p className="team-workspace__withheld">该产出未通过当前权限或完整性校验，正文与引用已隐藏。</p> : null}
    {output ? <OutputDetail output={output} onOpenCitation={onOpenCitation} /> : task.output_state === "none" ? <p className="team-workspace__empty">该任务尚无可展示产出。</p> : null}
    <button type="button" disabled={!canMutate || readOnly || pending || !["failed", "blocked"].includes(task.status)} onClick={() => onRetry(task)}>重试{professionalRoleLabels[task.role]}任务</button>
    <AttemptList attempts={task.attempts} />
  </div>;
}

function Timeline({ roles, taskByRole, messageHistory, nativeContent, onInspectTask }: {
  roles: ProfessionalRole[];
  taskByRole: Map<ProfessionalRole, TeamTask>;
  messageHistory: ReactNode;
  nativeContent: ReactNode;
  onInspectTask: (task: TeamTask) => void;
}): ReactElement {
  const tasks = roles.map((role) => taskByRole.get(role)).filter((task): task is TeamTask => Boolean(task))
    .sort((left, right) => left.updated_at.localeCompare(right.updated_at) || left.id.localeCompare(right.id));
  return <div className="team-workspace__timeline">
    {tasks.length ? tasks.map((task) => <article className="team-workspace__event" key={task.id}>
      <time dateTime={task.updated_at}>{formatTime(task.updated_at)}</time>
      <div>
        <header><h3>{professionalRoleLabels[task.role]}</h3><span className={`team-workspace__badge team-workspace__badge--${task.status}`}>{taskStatusLabels[task.status]}</span></header>
        <p>{task.output?.content.summary ?? task.instruction}</p>
        <small>版本 {task.revision} · 第 {task.attempt} 轮 · {task.attempts.length} 次模型尝试</small>
        <button type="button" onClick={() => onInspectTask(task)}>查看任务详情</button>
      </div>
    </article>) : <p className="team-workspace__empty">当前筛选下没有团队任务。</p>}
    <details className="team-workspace__embedded">
      <summary>已确认的研究请求</summary>
      {messageHistory}
    </details>
    <details className="team-workspace__embedded">
      <summary>原生 Gateway 执行内容</summary>
      {nativeContent}
    </details>
  </div>;
}

function Outputs({ roles, taskByRole, selectedTask, taskById, nativeContent, onOpenCitation, onInspectTask }: {
  roles: ProfessionalRole[];
  taskByRole: Map<ProfessionalRole, TeamTask>;
  selectedTask: TeamTask | null;
  taskById: Map<string, TeamTask>;
  nativeContent: ReactNode;
  onOpenCitation: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void;
  onInspectTask: (task: TeamTask) => void;
}): ReactElement {
  const tasks = roles.map((role) => taskByRole.get(role)).filter((task): task is TeamTask => Boolean(task));
  return <div className="team-workspace__outputs">
    {selectedTask ? <article className="team-workspace__output-card team-workspace__output-card--selected">
      <header><h3>已定位任务：{professionalRoleLabels[selectedTask.role]} · 版本 {selectedTask.revision}</h3><span>{selectedTask.id}</span></header>
      <TaskFullDetail task={selectedTask} taskById={taskById} readOnly pending={false} canMutate={false}
        onRetry={() => undefined} onOpenCitation={onOpenCitation} onInspectTask={onInspectTask} />
    </article> : null}
    {tasks.length ? tasks.map((task) => <article className="team-workspace__output-card" key={task.id}>
      <header><h3>{professionalRoleLabels[task.role]}产出</h3><span>{task.output?.id ? `产出 ${shortId(task.output.id)}` : "暂无产出"}</span></header>
      {task.output_state === "available" && task.output ? <OutputDetail output={task.output} onOpenCitation={onOpenCitation} /> :
        <p className="team-workspace__empty">{task.output_state === "withheld" ? "产出已隐藏。" : "该角色尚无可展示产出。"}</p>}
      <AttemptList attempts={task.attempts} />
    </article>) : <p className="team-workspace__empty">当前筛选下没有角色产出。</p>}
    <details className="team-workspace__embedded">
      <summary>原生 Gateway 内容</summary>
      {nativeContent}
    </details>
  </div>;
}

function OutputDetail({ output, onOpenCitation }: { output: TeamOutput; onOpenCitation: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void }): ReactElement {
  return <div className="team-workspace__output">
    <section><h4>摘要</h4><p>{output.content.summary}</p></section>
    <section><h4>发现</h4>{output.content.findings.length ? <ol>{output.content.findings.map((finding, index) => <li key={index}>
      <p><strong>{basisLabels[finding.basis]}：</strong>{finding.statement}</p>
      {finding.citations.length ? <div className="team-workspace__citations">{finding.citations.map((citation, citationIndex) =>
        <button key={`${citation.evidence_link_id}-${citationIndex}`} type="button" onClick={(event) => onOpenCitation(citation, output, event.currentTarget)}>
          引用 {citationIndex + 1}：{shortId(citation.evidence_link_id)}
        </button>)}</div> : <small>该发现未声明逐句引用。</small>}
    </li>)}</ol> : <p>未列出发现。</p>}</section>
    <SimpleList title="证据缺口" items={output.content.gaps} empty="未列出证据缺口。" />
    <SimpleList title="局限" items={output.content.limitations} empty="未列出局限。" />
    <section><h4>质控检查</h4>{output.content.checks.length ? <ul>{output.content.checks.map((check, index) =>
      <li key={index}>{checkKindLabels[check.kind]} · {checkStatusLabels[check.status]}：{check.detail}</li>)}</ul> : <p>未列出质控检查。</p>}</section>
    <details><summary>产出与依赖标识</summary><p>产出：{output.id}</p><p>证据：{output.evidence_ids.join("、") || "无"}</p><p>前序产出：{output.dependency_output_ids.join("、") || "无"}</p></details>
  </div>;
}

function ProblemPanel({ problems, onAskRole, onInspectTask }: { problems: ProblemItem[]; onAskRole?: (role: ProfessionalRole) => void; onInspectTask: (task: TeamTask) => void }): ReactElement {
  return <section className="team-workspace__side-stack" aria-labelledby="team-problems-heading">
    <h2 id="team-problems-heading">待解决问题</h2>
    {problems.length ? problems.map((problem) => <article className={`team-workspace__problem team-workspace__problem--${problem.severity}`} key={problem.id}>
      <span>{problem.severity === "blocker" ? "阻塞" : problem.severity === "warning" ? "待核对" : "缺口"}</span>
      <h3>{problem.title}</h3>
      <p>{problem.detail}</p>
      <div>{problem.task ? <button type="button" onClick={() => onInspectTask(problem.task!)}>查看任务</button> : null}
        {problem.role ? <button type="button" onClick={() => onAskRole?.(problem.role!)}>询问{roleShortLabels[problem.role]}</button> : null}</div>
    </article>) : <p className="team-workspace__empty">当前版本没有已保存的缺口、阻塞检查或失败原因。</p>}
  </section>;
}

function ReviewPanel({ team, visibleRevision, outputIds, readOnly, stale, pending, canReview, decision, setDecision, comment, setComment, onReview }: {
  team: GatewayTeam;
  visibleRevision: number;
  outputIds: string[];
  readOnly: boolean;
  stale: boolean;
  pending: boolean;
  canReview: boolean;
  decision: "approved" | "changes_requested";
  setDecision: (decision: "approved" | "changes_requested") => void;
  comment: string;
  setComment: (comment: string) => void;
  onReview: () => void;
}): ReactElement {
  const current = team.reviews.filter((review) => review.revision === visibleRevision);
  const history = team.reviews.filter((review) => review.revision < visibleRevision);
  const later = team.reviews.filter((review) => review.revision > visibleRevision);
  const qualityTask = tasksForRevision(team, visibleRevision).find((task) => task.role === "quality");
  const quality = qualityTask?.output_state === "available" ? qualityTask.output : null;
  return <section className="team-workspace__side-stack" aria-labelledby="team-review-heading">
    <h2 id="team-review-heading">审查状态</h2>
    <article className="team-workspace__ai-review">
      <h3>AI 质控意见</h3>
      <p>{qualityTask ? taskStatusLabels[qualityTask.status] : "尚未开始"} · 供研究员复核</p>
      {quality ? <><p className="team-workspace__ai-review-summary">{quality.content.summary}</p>
        <div className="team-workspace__check-labels">{quality.content.checks.map((check) => <span key={check.kind}>{checkKindLabels[check.kind]}：{checkStatusLabels[check.status]}</span>)}</div>
        <details><summary>查看 AI 检查明细</summary><p>{quality.content.summary}</p><ul>{quality.content.checks.map((check) => <li key={check.kind}>{checkKindLabels[check.kind]} · {checkStatusLabels[check.status]}：{check.detail}</li>)}</ul></details>
      </> : <p>完整检查意见将在质控任务产出后显示。</p>}
      <small>AI 质控完成不等于人工审核通过。</small>
    </article>
    <form className="team-workspace__review-form" onSubmit={(event) => { event.preventDefault(); onReview(); }}>
      {outputIds.length === 4 ? <details><summary>绑定本版本的 4 个角色产出</summary><p>{outputIds.join("、")}</p></details> : <p>尚缺 {4 - outputIds.length} 个当前产出</p>}
      <label>人工复核决定
        <select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)} disabled={readOnly || pending}>
          <option value="approved">通过</option>
          <option value="changes_requested">要求修改</option>
        </select>
      </label>
      <label>人工复核评语
        <textarea value={comment} onChange={(event) => setComment(event.target.value)} maxLength={4000} disabled={readOnly || pending} />
      </label>
      <button type="submit" disabled={!canReview}>保存人工复核</button>
      <small>{stale ? "团队状态待更新，恢复读取后才能保存人工复核。" : readOnly ? "历史版本只读；人工复核只能保存到当前团队版本。" : "必须绑定四个当前角色产出，且评语不能为空。"}</small>
    </form>
    <ReviewHistory title="当前版本复核记录" reviews={current} />
    {history.length ? <ReviewHistory title="不可变历史复核" reviews={history} /> : null}
    {later.length ? <details><summary>后续版本复核（不属于当前版本）</summary><ReviewHistory title="后续版本复核记录" reviews={later} /></details> : null}
  </section>;
}

function ReviewHistory({ title, reviews }: { title: string; reviews: GatewayTeam["reviews"] }): ReactElement {
  return <section className="team-workspace__review-history">
    <h3>{title}</h3>
    {reviews.length ? <ol>{reviews.map((review) => <li key={review.id}>
      <p>版本 {review.revision} · {reviewDecisionLabels[review.decision]} · {review.reviewed_by}</p>
      <p>{review.comment}</p>
      <details><summary>绑定产出</summary><p>{review.output_ids.join("、")}</p></details>
    </li>)}</ol> : <p>暂无记录。</p>}
  </section>;
}

function AttemptList({ attempts }: { attempts: TeamTask["attempts"] }): ReactElement {
  return <details className="team-workspace__attempts"><summary>模型调用与用量（{attempts.length} 次尝试）</summary>{attempts.length ? <ol>{attempts.map((attempt) => <li key={`${attempt.call_id}-${attempt.attempt}`}>
    <p>{attempt.operation} · {attempt.outcome} · {attempt.provider}</p>
    <p>请求模型：{attempt.requested_model} · 实际模型：{attempt.model ?? "未知"} · finish：{attempt.finish_reason ?? "未知"}</p>
    <p>Prompt token：{tokenLabel(attempt.prompt_tokens)} · Completion token：{tokenLabel(attempt.completion_tokens)} · 总 token：{tokenLabel(attempt.total_tokens)}</p>
    <p>耗时：{Math.round(attempt.latency_ms)}ms · 用量状态：{attempt.usage_status === "unknown" ? "未知" : attempt.usage_status}{attempt.retryable ? ` · 可重试${attempt.retry_delay_seconds ? `，建议等待 ${attempt.retry_delay_seconds}s` : ""}` : ""}</p>
    {attempt.repaired ? <p className="team-workspace__notice">本次调用经过结构修复后入库。</p> : null}
  </li>)}</ol> : <p>尚无模型调用记录。</p>}</details>;
}

function DependencyLinks({ task, taskById, onInspectTask }: { task: TeamTask; taskById: Map<string, TeamTask>; onInspectTask: (task: TeamTask) => void }): ReactElement {
  return <span className="team-workspace__dependency-links">{task.dependency_ids.map((id) => {
    const dependency = taskById.get(id);
    return dependency ? <button key={id} type="button" onClick={() => onInspectTask(dependency)}>查看前序产出：{professionalRoleLabels[dependency.role]} · 版本 {dependency.revision}</button>
      : <span key={id}>前序任务不可定位：{id}</span>;
  })}</span>;
}

function GoalRow({ goal }: { goal: { text: string; createdAt: string } }): ReactElement {
  return <article className="team-workspace__goal">
    <time dateTime={goal.createdAt}>{formatTime(goal.createdAt)}</time>
    <div><span className="team-workspace__avatar" aria-hidden="true">研</span><p><strong>研究目标</strong>{goal.text}</p></div>
  </article>;
}

function ReadState({ state, onRefresh }: { state?: TeamReadState; onRefresh?: () => void }): ReactElement {
  return <section className="team-workspace__read-state" aria-labelledby="team-workspace-read-state-heading">
    <h2 id="team-workspace-read-state-heading">专业团队状态</h2>
    <p>{readStateMessage(state)}</p>
    {state?.kind !== "unsupported" ? <button type="button" onClick={onRefresh}>重新读取团队</button> : null}
  </section>;
}

function EmptySidePanel({ title, copy }: { title: string; copy: string }): ReactElement {
  return <section className="team-workspace__side-stack"><h2>{title}</h2><p className="team-workspace__empty">{copy}</p></section>;
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }): ReactElement {
  return <button type="button" role="tab" aria-selected={active} tabIndex={active ? 0 : -1} onKeyDown={navigateTab} onClick={onClick}>{children}</button>;
}

function RightTabButton({ tab, active, setRightTab, children }: { tab: RightTab; active: boolean; setRightTab: (tab: RightTab) => void; children: ReactNode }): ReactElement {
  return <button type="button" role="tab" aria-selected={active} tabIndex={active ? 0 : -1} onKeyDown={navigateTab} onClick={() => setRightTab(tab)}>{children}</button>;
}

function navigateTab(event: KeyboardEvent<HTMLButtonElement>): void {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
  if (!tabs.length) return;
  const current = tabs.indexOf(event.currentTarget);
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  event.preventDefault();
  tabs[next]?.click();
  tabs[next]?.focus();
}

function RoleFilterButton({ value, roleFilter, setRoleFilter, children }: { value: RoleFilter; roleFilter: RoleFilter; setRoleFilter: (value: RoleFilter) => void; children: ReactNode }): ReactElement {
  return <button type="button" aria-pressed={roleFilter === value} onClick={() => setRoleFilter(value)}>{children}</button>;
}

type ProblemItem = { id: string; severity: "blocker" | "warning" | "gap"; title: string; detail: string; role?: ProfessionalRole; task?: TeamTask };

function deriveProblems(team: GatewayTeam, visibleRevision: number, taskByRole: Map<ProfessionalRole, TeamTask>, nativeStatus?: GatewayNativeRunStatus): ProblemItem[] {
  const problems: ProblemItem[] = [];
  if (team.status === "not_started") problems.push({ id: "team-not-started", severity: "gap", title: "专业团队未启动", detail: "尚未创建产业、财务、策略和质控任务。" });
  if (nativeStatus === "failed" || nativeStatus === "cancelled") problems.push({ id: `native-${nativeStatus}`, severity: "blocker", title: "原生 Gateway 未完成", detail: `原生 Gateway 当前为${nativeStatusLabels[nativeStatus]}，专业角色可能缺少冻结材料。` });
  for (const role of professionalRoles) {
    const task = taskByRole.get(role);
    if (!task) {
      problems.push({ id: `missing-${role}`, severity: "gap", title: `${professionalRoleLabels[role]}缺少当前任务`, detail: `版本 ${visibleRevision} 没有可展示的本角色任务。`, role });
      continue;
    }
    if (task.status === "blocked" || task.status === "failed") problems.push({ id: `status-${task.id}`, severity: "blocker", title: `${professionalRoleLabels[role]}${taskStatusLabels[task.status]}`, detail: task.reason_code ? reasonNext(task.reason_code) : "请查看任务尝试、依赖和证据缺口后再决定重试。", role, task });
    if (task.output_state === "withheld") problems.push({ id: `withheld-${task.id}`, severity: "blocker", title: `${professionalRoleLabels[role]}产出已隐藏`, detail: "该产出未通过当前权限或完整性校验。", role, task });
    if (task.output_state === "none") problems.push({ id: `output-none-${task.id}`, severity: "gap", title: `${professionalRoleLabels[role]}尚无产出`, detail: "角色任务已存在，但还没有可展示的 AI 草案。", role, task });
    const output = task.output_state === "available" ? task.output : null;
    output?.content.gaps.forEach((gap, index) => problems.push({ id: `gap-${task.id}-${index}`, severity: "gap", title: `${professionalRoleLabels[role]}证据缺口`, detail: gap, role, task }));
    output?.content.checks.filter((check) => check.status !== "pass").forEach((check, index) => problems.push({ id: `check-${task.id}-${index}`, severity: check.status === "blocker" ? "blocker" : "warning", title: `${checkKindLabels[check.kind]}${checkStatusLabels[check.status]}`, detail: check.detail, role, task }));
    if (task.reason_code && task.status !== "blocked" && task.status !== "failed") problems.push({ id: `reason-${task.id}`, severity: "warning", title: `${professionalRoleLabels[role]}原因：${reasonTitle(task.reason_code)}`, detail: reasonNext(task.reason_code), role, task });
  }
  return problems;
}

function taskIssue(task: TeamTask): { label: string; text: string } {
  const output = task.output_state === "available" ? task.output : null;
  const blocker = output?.content.checks.find((check) => check.status === "blocker");
  const warning = output?.content.checks.find((check) => check.status === "warning");
  if (blocker) return { label: "阻塞", text: blocker.detail };
  if (task.reason_code) return { label: reasonTitle(task.reason_code), text: reasonNext(task.reason_code) };
  if (output?.content.gaps[0]) return { label: "下一缺口", text: output.content.gaps[0] };
  if (warning) return { label: "待核对", text: warning.detail };
  if (task.status === "queued" || task.status === "running") return { label: "下一步", text: "等待当前角色完成任务并写入产出。" };
  if (task.output_state === "withheld") return { label: "产出隐藏", text: "该产出未通过当前权限或完整性校验。" };
  return { label: "下一步", text: "等待研究员复核 AI 草案并决定是否继续补证。" };
}

function SimpleList({ title, items, empty }: { title: string; items: string[]; empty: string }): ReactElement {
  return <section><h4>{title}</h4>{items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}

function teamProgressLabel(team: GatewayTeam, tasks: TeamTask[]): string {
  if (team.status === "paused") return "已暂停";
  if (team.status === "cancelled") return "已取消";
  if (team.status === "not_started") return "未启动";
  if (tasks.some((task) => task.status === "running" || task.status === "queued")) return "处理中";
  if (tasks.some((task) => task.status === "failed" || task.status === "blocked")) return "有待处理任务";
  if (tasks.length >= professionalRoles.length && tasks.every((task) => task.status === "succeeded")) return "本轮角色已完成";
  if (!tasks.length) return "等待任务";
  return teamStatusLabels[team.status];
}

function readStateTimelineMessage(state?: TeamReadState): string {
  return `${readStateMessage(state)} 协作时间线暂不可读。`;
}

function readStateMessage(state?: TeamReadState): string {
  if (!state) return "本区域等待绑定 Gateway 专业团队状态。";
  if (state.kind === "loading") return "正在读取专业团队…";
  if (state.kind === "unsupported") return "当前客户端尚未启用专业团队接口。";
  if (state.kind === "expired") return "本轮专业团队已失效，旧内容已清除。";
  if (state.kind === "unauthorized") return "专业团队访问权限已失效，旧内容已清除。";
  if (state.kind === "error" && state.schema) return "专业团队响应校验未通过，已隐藏不一致内容。";
  return "专业团队暂时无法读取。";
}

function clampRevision(selectedRevision: number | undefined, latest: number): number {
  if (latest <= 0) return 0;
  return Math.min(Math.max(selectedRevision ?? latest, 1), latest);
}

function toggleExpanded(current: Set<ProfessionalRole>, role: ProfessionalRole): Set<ProfessionalRole> {
  const next = new Set(current);
  if (next.has(role)) next.delete(role);
  else next.add(role);
  return next;
}

function reasonTitle(code: string): string {
  return reasonGuidance[code]?.title ?? "任务未完成";
}

function reasonNext(code: string): string {
  return reasonGuidance[code]?.next ?? "请查看上游状态、证据与模型调用记录后再决定重试或补充指令。";
}

function shortId(id: string): string {
  return id.slice(-12);
}

function tokenLabel(value: number | null): string {
  return value === null ? "未知" : String(value);
}

function draftLabel(role: ProfessionalRole): string {
  return role === "quality" ? "质控草案" : `${roleShortLabels[role]}分析草案`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function focusEvidencePanel(root: HTMLDivElement | null): void {
  if (!root || root.hidden) return;
  const target = root.querySelector<HTMLElement>(".research-evidence-original h3")
    ?? root.querySelector<HTMLElement>("aside, [tabindex], button, a, h2, h3");
  if (!target) return;
  if (!target.hasAttribute("tabindex") && !/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(target.tagName)) {
    target.setAttribute("tabindex", "-1");
  }
  target.focus();
}

function focusComposer(): void {
  document.querySelector<HTMLTextAreaElement>(".team-workspace .gateway-composer textarea, .team-workspace textarea")?.focus();
}
