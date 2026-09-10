import { useState, type ReactElement } from "react";

import type { GatewayNativeRunStatus } from "@/gateway/contracts";
import {
  professionalRoleLabels,
  professionalRoles,
  tasksForRevision,
  type GatewayTeam,
  type TeamCitation,
  type TeamOutput,
  type TeamTask,
} from "@/gateway/team";
import type { TeamMutation, TeamMutationController } from "@/gateway/useTeamMutation";
import type { TeamReadState } from "@/gateway/useGatewayTeam";

const roleMarkers = { industry: "产", finance: "财", strategy: "策", quality: "审" } as const;
const unboundRoleTitles = { industry: "分析师-产业", finance: "分析师-财务", strategy: "分析师-策略", quality: "质控审核员（AI）" } as const;
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
const checkKindLabels = {
  citations: "引用",
  periods: "期间",
  units: "口径",
  source_independence: "来源独立性",
  counter_evidence: "反证",
  completeness: "完整性",
} as const;
const checkStatusLabels = { pass: "通过", warning: "提示", blocker: "阻塞" } as const;
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

type Props = {
  state?: TeamReadState;
  selectedRevision?: number;
  onSelectRevision?: (revision: number) => void;
  mutation?: Pick<TeamMutationController, "busy" | "notice" | "unknown" | "submit" | "retry">;
  onOpenCitation?: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void;
  onRefresh?: () => void;
  nativeStatus?: GatewayNativeRunStatus;
};

type Role = typeof professionalRoles[number];

export function ProfessionalRoleFrame(props: Props = {}): ReactElement {
  if (!props.state) return <UnboundFrame />;
  return <BoundFrame {...props} state={props.state} />;
}

function UnboundFrame(): ReactElement {
  return (
    <section className="professional-role-frame" aria-labelledby="professional-role-heading">
      <header className="professional-role-frame__header">
        <div>
          <h2 id="professional-role-heading">专业投研分工</h2>
          <p>发起研究后，本轮会进入产业、财务、策略、AI 质控四个角色队列；这里只显示 Gateway 已保存的真实任务与产出。</p>
        </div>
        <span className="professional-role-frame__badge">四角色流程</span>
      </header>
      <details className="professional-role-planning">
        <summary>研究创建后：产业 · 财务 · 策略 · 质控</summary>
        <div className="professional-role-frame__cards">
          {professionalRoles.map((role) => (
            <article className="professional-role-card" key={role}>
              <span className={`professional-role-card__marker professional-role-card__marker--${role}`} aria-hidden="true">{roleMarkers[role]}</span>
              <div><h3>{unboundRoleTitles[role]}</h3><p>等待创建真实角色任务</p></div>
            </article>
          ))}
        </div>
      </details>
    </section>
  );
}

function BoundFrame({ state, selectedRevision, onSelectRevision, mutation, onOpenCitation, onRefresh, nativeStatus }: Props & { state: TeamReadState }): ReactElement {
  const [selectedRole, setSelectedRole] = useState<Role>("industry");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [decision, setDecision] = useState<"approved" | "changes_requested">("approved");
  const [comment, setComment] = useState("");
  if (state.kind !== "available") return <ReadStateFrame state={state} onRefresh={onRefresh} />;

  const team = state.data;
  const visibleRevision = Math.min(Math.max(selectedRevision ?? team.revision, team.revision === 0 ? 0 : 1), team.revision);
  const tasks = tasksForRevision(team, visibleRevision);
  const taskByRole = new Map(tasks.map((task) => [task.role, task]));
  const taskById = new Map(team.tasks.map((task) => [task.id, task]));
  const selectedTask = selectedTaskId ? taskById.get(selectedTaskId) : null;
  const activeTask = selectedTask && selectedTask.revision <= visibleRevision ? selectedTask : taskByRole.get(selectedRole) ?? tasks[0] ?? null;
  const outputIds = professionalRoles.map((role) => taskByRole.get(role)?.output?.id).filter((id): id is string => Boolean(id));
  const stale = Boolean(state.stale);
  const readOnly = stale || team.revision === 0 || visibleRevision !== team.revision;
  const canReview = !readOnly && outputIds.length === 4 && comment.trim().length > 0 && !mutation?.busy;
  const revisions = Array.from({ length: Math.max(team.revision, 1) }, (_, index) => index + 1);

  function submit(operation: TeamMutation): void {
    if (stale || mutation?.busy) return;
    void mutation?.submit(operation);
  }
  function command(kind: "start" | "pause" | "resume" | "cancel"): void {
    submit({ kind: "command", body: { kind, expected_revision: team.revision } });
  }
  function review(): void {
    if (!canReview) return;
    submit({ kind: "review", body: { expected_revision: team.revision, output_ids: outputIds, decision, comment: comment.trim() } });
  }

  return (
    <section className="professional-role-frame professional-role-frame--bound" aria-labelledby="professional-role-heading">
      <header className="professional-role-frame__header">
        <div>
          <h2 id="professional-role-heading">专业投研分工</h2>
          <p>{stale ? "上次读取的" : ""}团队状态：{teamStatusLabels[team.status]} · 当前团队版本 {team.revision} · 事件序列 {team.event_sequence}{state.refreshing ? " · 正在刷新" : ""}</p>
          <p>暂停仅影响专业团队；原生 Gateway 状态：{nativeStatus ? nativeStatusLabels[nativeStatus] : "未提供"}。</p>
        </div>
        <span className="professional-role-frame__badge">真实团队</span>
      </header>
      <div className="professional-role-controls" aria-label="专业团队控制">
        <label>团队版本
          <select value={visibleRevision} onChange={(event) => onSelectRevision?.(Number(event.target.value))} disabled={team.revision === 0}>
            {revisions.map((revision) => <option value={revision} key={revision}>版本 {revision}</option>)}
          </select>
        </label>
        {team.status === "not_started" ? <button type="button" disabled={stale || mutation?.busy} onClick={() => command("start")}>启动专业团队</button> : null}
        {team.status === "active" ? <button type="button" disabled={readOnly || mutation?.busy} onClick={() => command("pause")}>暂停专业团队</button> : null}
        {team.status === "paused" ? <button type="button" disabled={readOnly || mutation?.busy} onClick={() => command("resume")}>恢复专业团队</button> : null}
        {team.status === "active" || team.status === "paused" ? <button type="button" disabled={readOnly || mutation?.busy} onClick={() => command("cancel")} aria-describedby="professional-cancel-scope">取消专业团队</button> : null}
        <button type="button" onClick={onRefresh}>重新读取团队</button>
        {team.status === "active" || team.status === "paused" ? <span id="professional-cancel-scope" className="professional-role-control-note">取消会同时取消仍在进行的原生 Gateway 研究；暂停只影响专业团队。</span> : null}
      </div>
      {stale ? <p className="professional-role-notice" role="status">团队状态待更新，当前显示上次成功读取的内容；恢复读取前暂停提交。可重新读取团队以重试。</p> : null}
      {mutation?.notice ? <p className="professional-role-notice" role={mutation.unknown ? "alert" : "status"}>{mutation.notice}{mutation.unknown ? <button type="button" onClick={() => { if (!stale && !mutation.busy) void mutation.retry(); }} disabled={stale || mutation.busy}>重试原请求</button> : null}</p> : null}
      {team.status === "not_started" ? <p className="gateway-empty-copy">本轮尚未启动专业团队。启动后才会创建产业、财务、策略和质控任务。</p> : null}
      <div className="professional-role-tabs" role="tablist" aria-label="专业角色">
        {professionalRoles.map((role) => {
          const task = taskByRole.get(role);
          return <button key={role} type="button" role="tab" aria-selected={selectedRole === role} onClick={() => { setSelectedRole(role); setSelectedTaskId(null); }}>
            {professionalRoleLabels[role]}<span>{task ? taskStatusLabels[task.status] : "无任务"}</span>
          </button>;
        })}
      </div>
      <div className="professional-role-detail" role="tabpanel">
        {activeTask ? <TaskDetail task={activeTask} taskById={taskById} onSelectTask={(task) => { setSelectedRole(task.role); setSelectedTaskId(task.id); }} onOpenCitation={onOpenCitation} onRetry={() => submit({ kind: "command", body: { kind: "retry", task_id: activeTask.id, expected_revision: team.revision } })} retryDisabled={readOnly || mutation?.busy || activeTask.status !== "failed"} />
          : <p className="gateway-empty-copy">当前版本没有可展示的{professionalRoleLabels[selectedRole]}任务。</p>}
      </div>
      <form className="professional-role-review" onSubmit={(event) => { event.preventDefault(); review(); }}>
        <label>人工复核决定
          <select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)} disabled={readOnly || mutation?.busy}>
            <option value="approved">通过</option>
            <option value="changes_requested">要求修改</option>
          </select>
        </label>
        <label>人工复核评语
          <textarea value={comment} onChange={(event) => setComment(event.target.value)} maxLength={4000} disabled={readOnly || mutation?.busy} />
        </label>
        <button type="submit" disabled={!canReview}>保存人工复核</button>
        {stale ? <p>团队状态待更新，恢复读取后才能保存人工复核。</p> : readOnly ? <p>历史版本只读；人工复核只能保存到当前团队版本。</p> : <p>需同时包含四个当前角色产出后才能保存人工复核。</p>}
      </form>
      <ReviewHistory reviews={team.reviews} visibleRevision={visibleRevision} />
    </section>
  );
}

function ReadStateFrame({ state, onRefresh }: { state: Exclude<TeamReadState, { kind: "available" }>; onRefresh?: () => void }): ReactElement {
  const message = state.kind === "loading" ? "正在读取专业团队…" : state.kind === "unsupported" ? "当前客户端尚未启用专业团队接口。" : state.kind === "expired" ? "本轮专业团队已失效，旧内容已清除。" : state.kind === "unauthorized" ? "专业团队访问权限已失效，旧内容已清除。" : state.kind === "error" && state.schema ? "专业团队响应校验未通过，已隐藏不一致内容。" : "专业团队暂时无法读取。";
  return <section className="professional-role-frame" aria-labelledby="professional-role-heading"><header className="professional-role-frame__header"><div><h2 id="professional-role-heading">专业投研分工</h2><p>{message}</p></div><span className="professional-role-frame__badge">读取中</span></header>{state.kind !== "unsupported" ? <button type="button" onClick={onRefresh}>重新读取团队</button> : null}</section>;
}

function TaskDetail({ task, taskById, onSelectTask, onOpenCitation, onRetry, retryDisabled }: {
  task: TeamTask;
  taskById: Map<string, TeamTask>;
  onSelectTask: (task: TeamTask) => void;
  onOpenCitation?: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void;
  onRetry: () => void;
  retryDisabled: boolean;
}): ReactElement {
  const output = task.output_state === "available" ? task.output : null;
  return <article className="professional-role-task">
    <header>
      <div><h3>{professionalRoleLabels[task.role]}</h3><p>任务版本 {task.revision} · {taskStatusLabels[task.status]} · 第 {task.attempt} 轮尝试</p></div>
      <button type="button" disabled={retryDisabled} onClick={onRetry}>重试{professionalRoleLabels[task.role]}任务</button>
    </header>
    {task.reason_code ? <ReasonNotice code={task.reason_code} /> : null}
    <details><summary>原始角色指令</summary><p>{task.instruction || "未提供指令。"}</p></details>
    <DependencyList task={task} taskById={taskById} onSelectTask={onSelectTask} />
    {task.output_state === "withheld" ? <p className="professional-role-withheld">该产出未通过当前权限或完整性校验，正文与引用已隐藏。</p> : null}
    {output ? <OutputDetail output={output} onOpenCitation={onOpenCitation} /> : task.output_state === "none" ? <p className="gateway-empty-copy">该任务尚无可展示产出。</p> : null}
    <AttemptList attempts={task.attempts} />
  </article>;
}

function DependencyList({ task, taskById, onSelectTask }: { task: TeamTask; taskById: Map<string, TeamTask>; onSelectTask: (task: TeamTask) => void }): ReactElement | null {
  if (!task.dependency_ids.length) return <p className="professional-role-meta">无前序角色依赖。</p>;
  return <div className="professional-role-dependencies"><h4>前序依赖</h4>{task.dependency_ids.map((id) => {
    const dependency = taskById.get(id);
    return dependency ? <button key={id} type="button" onClick={() => onSelectTask(dependency)}>查看前序产出：{professionalRoleLabels[dependency.role]} · 版本 {dependency.revision}</button>
      : <span key={id}>前序任务不可定位：{id}</span>;
  })}</div>;
}

function OutputDetail({ output, onOpenCitation }: { output: TeamOutput; onOpenCitation?: (citation: TeamCitation, output: TeamOutput, trigger: HTMLElement) => void }): ReactElement {
  return <div className="professional-role-output">
    <section><h4>摘要</h4><p>{output.content.summary}</p></section>
    <section><h4>发现</h4>{output.content.findings.length ? <ol>{output.content.findings.map((finding, index) => <li key={index}>
      <p><strong>{basisLabels[finding.basis]}：</strong>{finding.statement}</p>
      {finding.citations.length ? <div className="professional-role-citations">{finding.citations.map((citation, citationIndex) => <button key={citation.evidence_link_id} type="button" onClick={(event) => onOpenCitation?.(citation, output, event.currentTarget)}>引用 {citationIndex + 1}：查看冻结原文</button>)}</div> : <small>该发现未声明逐句引用。</small>}
    </li>)}</ol> : <p>未列出发现。</p>}</section>
    <SimpleList title="证据缺口" items={output.content.gaps} empty="未列出证据缺口。" />
    <SimpleList title="局限" items={output.content.limitations} empty="未列出局限。" />
    <section><h4>质控检查</h4>{output.content.checks.length ? <ul>{output.content.checks.map((check, index) => <li key={index}>{checkKindLabels[check.kind]} · {checkStatusLabels[check.status]}：{check.detail}</li>)}</ul> : <p>未列出质控检查。</p>}</section>
    <details><summary>核对产出标识</summary><p>产出：{output.id}</p><p>证据：{output.evidence_ids.join("、") || "无"}</p><p>前序产出：{output.dependency_output_ids.join("、") || "无"}</p></details>
  </div>;
}

function AttemptList({ attempts }: { attempts: TeamTask["attempts"] }): ReactElement {
  return <details className="professional-role-attempts"><summary>模型调用与用量（{attempts.length} 次尝试）</summary>{attempts.length ? <ol>{attempts.map((attempt) => <li key={`${attempt.call_id}-${attempt.attempt}`}>
    <p>{attempt.operation} · {attempt.outcome} · {attempt.provider} · 请求模型：{attempt.requested_model} · 实际模型：{attempt.model ?? "未知"}</p>
    <p><span>Prompt token：{tokenLabel(attempt.prompt_tokens)}</span> · <span>Completion token：{tokenLabel(attempt.completion_tokens)}</span> · <span>总 token：{tokenLabel(attempt.total_tokens)}</span></p>
    <p>耗时：{Math.round(attempt.latency_ms)}ms · 用量状态：{attempt.usage_status === "unknown" ? "未知" : attempt.usage_status}</p>
    {attempt.repaired ? <p className="professional-role-notice">本次调用经过结构修复后入库。</p> : null}
  </li>)}</ol> : <p>尚无模型调用记录。</p>}</details>;
}

function ReasonNotice({ code }: { code: string }): ReactElement {
  const guidance = reasonGuidance[code] ?? { title: "任务未完成", next: "请查看上游状态、证据与模型调用记录后再决定重试或补充指令。" };
  return <div className="professional-role-notice">
    <p>{guidance.title}：{guidance.next}</p>
    <details><summary>技术原因代码</summary><p>{code}</p></details>
  </div>;
}

function ReviewHistory({ reviews, visibleRevision }: { reviews: GatewayTeam["reviews"]; visibleRevision: number }): ReactElement {
  const current = reviews.filter((review) => review.revision === visibleRevision);
  const other = reviews.filter((review) => review.revision !== visibleRevision);
  return <section className="professional-role-review-history" aria-labelledby="professional-review-history-heading">
    <h3 id="professional-review-history-heading">已保存人工复核</h3>
    {current.length ? <ol>{current.map((review) => <ReviewItem review={review} key={review.id} />)}</ol> : <p>当前版本尚无已保存人工复核。</p>}
    {other.length ? <section className="professional-role-review-history__other"><h4>其他版本复核记录（{other.length} 条）</h4><ol>{other.map((review) => <ReviewItem review={review} key={review.id} />)}</ol></section> : null}
  </section>;
}

function ReviewItem({ review }: { review: GatewayTeam["reviews"][number] }): ReactElement {
  return <li>
    <p>版本 {review.revision} · {reviewDecisionLabels[review.decision]} · {review.reviewed_by}</p>
    <p>{review.comment}</p>
    <details><summary>绑定产出</summary><p>{review.output_ids.join("、")}</p></details>
  </li>;
}

function SimpleList({ title, items, empty }: { title: string; items: string[]; empty: string }): ReactElement {
  return <section><h4>{title}</h4>{items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}

function tokenLabel(value: number | null): string {
  return value === null ? "未知" : String(value);
}
