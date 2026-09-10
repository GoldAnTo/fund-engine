import { useEffect, useState, type ReactElement, type ReactNode } from "react";
import type { GatewayConnectionState, GatewayExecutionProgress, GatewayExecutionTask, GatewayRun } from "@/gateway/contracts";

const taskLabels: Record<GatewayExecutionTask["taskType"], string> = {
  support: "支持验证", contradict: "反证检验", alternative: "替代解释", alternative_explanation: "替代解释", intake_material: "材料核验",
};
const statusLabels: Record<GatewayExecutionTask["status"], string> = {
  queued: "排队中", running: "执行中", retry_wait: "等待重试", succeeded: "已完成", partial: "部分完成", failed: "执行失败", cancelled: "已取消",
};
const stageLabels: Record<GatewayExecutionTask["stage"], string> = {
  queued: "等待领取任务", searching: "检索合规来源", fetching: "读取来源内容", freezing: "保存原始材料",
  extracting: "提取材料中的陈述", admitting: "校验证据准入", succeeded: "处理完成", partial: "处理结束，有缺口", failed: "处理失败", cancelled: "已停止",
};
const providerLabels = { gildata: "巨灵数据", sse: "上交所", szse: "深交所" };
const actionLabels: Record<GatewayExecutionProgress["nextAction"], string> = {
  wait_for_execution: "后台正在调度或处理任务，进展会自动更新。",
  wait_for_retry: "后台已安排重试，具体时间见任务明细。",
  check_execution: "需要检查后台执行状态；刷新页面只重新读取状态，不会重新启动任务。",
  review_result: "本轮执行已结束，请核对证据与研究结果。",
  none: "暂无待执行操作。",
};
const reasonLabels: Record<NonNullable<GatewayExecutionProgress["reasonCode"]>, string> = {
  source_unavailable: "资料处理尚未取得可用结果，请检查任务状态和异常数量。",
  source_policy_blocked: "当前来源不符合本轮资料策略，不能继续用于证据。",
  execution_state_unavailable: "执行明细暂无法通过授权或一致性检查，已隐藏任务数据。",
  native_execution_failed: "本轮执行未能完成，未完成的任务不能视为已取得证据。",
  worker_unavailable: "来源处理服务没有及时报告心跳。任务尚未完成，需要恢复后台执行。",
  research_subject_missing: "未识别到研究主体，本轮缺少证据匹配所需的公司名称，继续等待不会补齐这个缺口。",
};
const terminal = new Set(["succeeded", "failed", "cancelled"]);
const taskPriority: Record<GatewayExecutionTask["status"], number> = {
  running: 0, retry_wait: 1, failed: 2, queued: 3, partial: 4, succeeded: 5, cancelled: 6,
};

function timeLabel(value: string): string {
  return new Date(value).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function elapsed(start: string, end: number): string {
  const seconds = Math.max(0, Math.floor((end - Date.parse(start)) / 1000));
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分钟`;
}

function taskStage(task: GatewayExecutionTask): string {
  if (task.sourceKind === "intake_material" && task.stage === "searching") return "准备你提交的材料";
  if (task.sourceKind === "intake_material" && task.stage === "fetching") return "读取你提交的材料";
  return stageLabels[task.stage];
}

export function GatewayExecutionPanel({ run, connection, renderTaskDetails }: { run: GatewayRun; connection: GatewayConnectionState; renderTaskDetails?: (task: GatewayExecutionTask) => ReactNode }): ReactElement | null {
  const [now, setNow] = useState(Date.now);
  const [showAllTasks, setShowAllTasks] = useState(false);
  const active = !terminal.has(run.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [active]);
  const execution = run.execution;
  if (!execution) return null;
  const ended = execution.tasks.filter((task) => terminal.has(task.status) || task.status === "partial").length;
  const failed = execution.tasks.filter((task) => task.status === "failed").length;
  const partial = execution.tasks.filter((task) => task.status === "partial").length;
  const running = execution.tasks.filter((task) => task.status === "running").length;
  const queued = execution.tasks.filter((task) => task.status === "queued").length;
  const counts = execution.tasks.reduce((total, task) => ({
    discovered: total.discovered + task.counts.discovered, fetched: total.fetched + task.counts.fetched,
    frozen: total.frozen + task.counts.frozen, admitted: total.admitted + task.counts.admitted,
    exceptions: total.exceptions + task.counts.exceptions,
  }), { discovered: 0, fetched: 0, frozen: 0, admitted: 0, exceptions: 0 });
  const hasMaterial = execution.tasks.some((task) => task.sourceKind === "intake_material");
  const currentReason = execution.reasonCode;
  const orderedTasks = execution.tasks.map((task, index) => ({ task, index }))
    .sort((left, right) => taskPriority[left.task.status] - taskPriority[right.task.status] || left.index - right.index);
  const visibleTasks = showAllTasks ? orderedTasks : orderedTasks.slice(0, 5);

  return <section className="gateway-execution" aria-labelledby="execution-heading">
    <header className="gateway-section-heading">
      <h2 id="execution-heading">运行进度</h2>
      <span className={`execution-worker execution-worker--${execution.workerState}`}>
        {execution.workerState === "online" ? "来源处理服务在线" : execution.workerState === "offline" ? "来源处理服务离线" : "来源处理服务状态未确认"}
      </span>
    </header>
    <p className="execution-overview">
      已结束 {ended} / {execution.tasks.length} 项任务
      {active ? ` · ${running} 项执行中 · ${queued} 项排队` : " · 本轮已结束"}
      {partial > 0 ? ` · 部分完成 ${partial} 项` : ""}
      {failed > 0 ? ` · 失败 ${failed} 项` : ""}
      {` · ${active ? "已耗时" : "执行时长"} ${elapsed(run.createdAt, active ? now : Date.parse(execution.updatedAt))}`}
    </p>
    <p className="execution-timestamps">
      最近进展 <time dateTime={execution.updatedAt}>{timeLabel(execution.updatedAt)}</time>
      {execution.workerLastSeenAt ? <> · 服务心跳 <time dateTime={execution.workerLastSeenAt}>{timeLabel(execution.workerLastSeenAt)}</time></> : null}
      <span>服务心跳不代表每个任务正在执行。</span>
    </p>
    {connection !== "live" ? <p className="execution-notice" role="status">实时连接暂未就绪，以下为最后收到的状态。</p> : null}
    {currentReason ? <div className="execution-notice" role="status"><p>{reasonLabels[currentReason]}</p><p>{currentReason === "research_subject_missing"
      ? "请在下方发送“调整范围：研究主体：公司名称；待核验材料：原始材料”，创建保留原记录的新一轮；刷新不会修复冻结范围。"
      : actionLabels[execution.nextAction]}</p></div> : null}
    {execution.projectionState === "available" ? <>
      <p className="execution-counts">发现来源 {counts.discovered} · 已读取 {counts.fetched} · 已冻结 {counts.frozen} · 已准入 {counts.admitted} · 异常 {counts.exceptions}</p>
      {hasMaterial ? <p className="execution-caveat">材料处理不等于外部事实核验。用户提供材料中的数字和观点仍待独立来源验证。</p> : null}
      {execution.tasks.length === 0 ? <p className="gateway-empty-copy">尚无可验证的资料任务。请以当前执行阶段为准。</p> :
        <div className="execution-task-list" role="list" aria-label="当前资料任务">
          {visibleTasks.map(({ task, index }) => <div className="execution-task" role="listitem" key={task.taskId}>
            <div className="execution-task__identity"><strong>{String(index + 1).padStart(2, "0")} {taskLabels[task.taskType]}</strong><span>{task.sourceName}</span></div>
            <div className="execution-task__step"><span>{taskStage(task)}</span><small>
              {task.sourceKind === "intake_material" ? "本地材料处理" : task.providers.length > 0
                ? `已调用：${task.providers.map((provider) => providerLabels[provider]).join("、")}` : "尚未调用外部来源"}
              {` · 尝试 ${task.attempt} 次`}
              {task.counts.exceptions > 0 ? ` · 已记录异常 ${task.counts.exceptions} 次，见任务历史` : ""}
            </small></div>
            <div className="execution-task__state"><span className={`execution-task-status execution-task-status--${task.status}`}>{statusLabels[task.status]}</span>
              <time dateTime={task.updatedAt}>{timeLabel(task.updatedAt)}</time>
              {task.retryAt ? <small>下次重试 {timeLabel(task.retryAt)}</small> : null}
            </div>
            {renderTaskDetails?.(task)}
          </div>)}
        </div>}
      {orderedTasks.length > 5 ? <button className="execution-expand" type="button" aria-expanded={showAllTasks} onClick={() => setShowAllTasks((value) => !value)}>
        {showAllTasks ? "收起任务明细" : `展开全部 ${orderedTasks.length} 项任务`}
      </button> : null}
      <p className="execution-caveat">任务计数来自后台记录，已准入不等于人工审核通过；同一材料可能用于多个验证任务。</p>
    </> : null}
  </section>;
}
