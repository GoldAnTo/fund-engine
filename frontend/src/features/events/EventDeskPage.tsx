import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import { researchOsApi, type ResearchWorkerStatus } from "../../app/researchOsApi";
import type { EventResearchListItem } from "../../domain/eventResearch";
import {
  eventDeskNeedsHumanReview,
  eventDeskRoute,
  isPreparationDeskEvent,
  isPreparationInProgress,
} from "../../domain/eventResearchPresentation";

function timeLabel(value: string): string { return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
const STATUS_LABEL: Record<EventResearchListItem["status"], string> = { extracting: "正在识别", researching: "系统研究中", awaiting_key_review: "等待证据审核", continuing: "系统继续研究", awaiting_scope: "需要调整范围", draft_ready: "等待结论审核", published: "结论已发布", exhausted: "范围已穷尽" };

function ResearchDispatchSkeleton() {
  return <div className="ros-dispatch-skeleton" aria-label="研究调度加载中" aria-busy="true">
    {[0, 1, 2, 3].map((index) => <div className="ros-dispatch-skeleton__row" data-testid="research-dispatch-skeleton" key={index}>
      <span /><div><i /><b /></div><em /><small />
    </div>)}
  </div>;
}

export function EventDeskPage() {
  const location = useLocation(); const [searchParams] = useSearchParams(); const [events, setEvents] = useState<EventResearchListItem[]>([]); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(true); const [workerStatus, setWorkerStatus] = useState<ResearchWorkerStatus | null>(null); const [workerStatusError, setWorkerStatusError] = useState(false);
  const loadEvents = () => {
    setError(null);
    setLoading(true);
    researchClient.listEventResearch().then(setEvents).catch(() => setError("暂时无法读取事件研究。请检查研究服务后重试。")).finally(() => setLoading(false));
  };
  useEffect(() => { loadEvents(); }, []);
  useEffect(() => {
    let active = true;
    researchOsApi.workerStatus().then((value) => {
      if (!active) return;
      setWorkerStatus(value); setWorkerStatusError(false);
    }).catch(() => {
      if (!active) return;
      setWorkerStatus(null); setWorkerStatusError(true);
    });
    return () => { active = false; };
  }, []);
  const attentionOnly = searchParams.get("attention") === "1"; const section = location.pathname === "/network" ? "network" : location.pathname === "/monitoring" ? "monitoring" : attentionOnly ? "attention" : "desk";
  const visibleEvents = useMemo(() => attentionOnly ? events.filter(eventDeskNeedsHumanReview) : events, [attentionOnly, events]);
  const ordered = useMemo(() => [...visibleEvents].sort((a, b) => Number(eventDeskNeedsHumanReview(b)) - Number(eventDeskNeedsHumanReview(a)) || b.updatedAt.localeCompare(a.updatedAt)), [visibleEvents]); const highest = ordered[0]; const preparing = ordered.filter(isPreparationInProgress); const preparationWaiting = ordered.filter((event) => isPreparationDeskEvent(event) && !isPreparationInProgress(event)); const active = ordered.filter((event) => !isPreparationDeskEvent(event) && ["extracting", "researching", "continuing"].includes(event.status)); const review = ordered.filter(eventDeskNeedsHumanReview);
  const title = section === "network" ? "研究网络：哪些判断正在改变？" : section === "monitoring" ? "监控与版本：哪些运行需要关注？" : section === "attention" ? "待我审核：先作出哪些判断？" : "今天，先推进哪一个判断？";
  const subtitle = section === "network" ? "以当前 Case 为入口查看证据、审核与市场表达；全局入口不会伪造跨 Case 的图谱关系。" : section === "monitoring" ? "查看所有 Case 的受控运行、人工待办和下一验证事件；每条记录可进入 Case 级运行详情。" : section === "attention" ? "这里只显示需要人工判断的 Case；系统不会代替你采纳证据或发布结论。" : "事件是入口，不是信息流。当前按是否需要人工判断与更新时间排序；基金披露暴露仅在已具备相应数据时另行展示。";
  const workerUnavailable = workerStatus?.status === "unavailable";
  const workerUnknown = workerStatusError || workerStatus?.status === "stale";
  return <main className="ros-page ros-desk"><header className="ros-page-head"><div><p className="ros-eyebrow">{section === "network" ? "研究资产 · Research Network" : section === "monitoring" ? "研究资产 · Monitoring" : "事件研究 · Research Desk"}</p><h1>{title}</h1><p>{subtitle}</p></div><Link className="ros-button ros-button--primary" to="/events/new">＋ 从事件开始</Link></header>{error && <section className="ros-empty ros-empty--compact" role="alert"><strong>研究调度暂不可读取</strong><p>{error}</p><button className="ros-button ros-button--secondary" type="button" onClick={loadEvents}>重试读取事件研究</button></section>}{!loading && !error && ordered.length === 0 && <section className="ros-empty ros-empty--large"><h2>{attentionOnly ? "没有待人工审核的 Case" : "还没有进行中的研究"}</h2><p>从公告、研报或新闻快照建立事件 Case；系统会先固定研究范围和可验证因素。</p><Link className="ros-button ros-button--primary" to="/events/new">从事件开始</Link></section>}{!!highest && <div className="ros-desk-layout"><section className="ros-card ros-case-queue" aria-label="研究优先队列"><header className="ros-card-head"><div><p className="ros-eyebrow">优先队列</p><h2>等待推进的判断</h2></div><span>{review.length} 项需要人工决定</span></header><Link className="ros-priority" to={eventDeskRoute(highest)}><span className="ros-eyebrow">当前优先</span><h2>{highest.nextHumanAction || highest.statusSummary}</h2><p>{highest.eventTitle} · {isPreparationDeskEvent(highest) ? "进入准备工作台后可查看系统进展或完成当前确认。" : "完成后决定当前研究是否需要调整范围或复核结论。"}</p><strong>{isPreparationDeskEvent(highest) ? "进入研究准备 →" : eventDeskNeedsHumanReview(highest) ? "进入审核 →" : "查看 Case →"}</strong></Link><div className="ros-queue-rows">{ordered.slice(1, 6).map((event, index) => <Link className="ros-queue-row" key={event.id} to={eventDeskRoute(event)}><span className={`ros-priority-number${eventDeskNeedsHumanReview(event) ? " is-review" : ""}`}>{index + 2}</span><div><strong>{event.eventTitle}</strong><p>{event.statusSummary}</p></div><span className={eventDeskNeedsHumanReview(event) ? "ros-need is-review" : "ros-need"}>{event.nextHumanAction || (isPreparationDeskEvent(event) ? event.statusSummary : workerUnavailable ? "等待执行器启动" : workerUnknown ? "执行器状态待确认" : "系统补证中")}</span></Link>)}</div></section><aside className="ros-desk-rail"><section><p className="ros-eyebrow">今天的工作</p><h2>先审核，再扩展</h2><p>系统不会自行采纳候选关系，也不会用同期行情自动写成因果结论。</p></section><section><p className="ros-eyebrow">研究网络</p><div className="ros-mini-row"><span>正式运行中 Case</span><b>{active.length || "—"}</b></div><div className="ros-mini-row"><span>系统准备中</span><b>{preparing.length || "—"}</b></div><div className="ros-mini-row"><span>准备待确认</span><b>{preparationWaiting.length || "—"}</b></div><div className="ros-mini-row"><span>待你审核</span><b>{review.length || "—"}</b></div><div className="ros-mini-row"><span>已发布结论</span><b>{ordered.filter((event) => event.status === "published").length || "—"}</b></div></section><section><p className="ros-eyebrow">系统状态</p><h2>{active.length && workerUnavailable ? "执行器未启动；已排队研究不会自动推进" : active.length && workerUnknown ? "执行器状态暂不可确认" : active.length ? "正在受控运行" : preparing.length ? "正在准备研究材料" : preparationWaiting.length ? "等待研究准备确认" : "等待下一项触发"}</h2><p>{active.length && workerUnavailable ? "范围和排队记录已保存；执行器恢复前不会领取任务，系统不会把它显示为正在执行。" : active.length && workerUnknown ? "存在进行中 Case，但执行器心跳或状态暂不可确认；请以运行档案和阶段事件为准。" : active.length ? `${active.length} 个 Case 正在按各自范围、来源许可和配置版本补证。` : preparing.length ? `${preparing.length} 个 Case 正在准备研究材料，尚未创建正式研究运行。` : preparationWaiting.length ? `${preparationWaiting.length} 个 Case 正在等待研究准备确认，正式研究尚未启动。` : "没有后台运行；所有新证据和任务状态都会在这里留下可查看的记录。"}</p></section></aside></div>}<section className="ros-all-events"><header><div><p className="ros-eyebrow">{attentionOnly ? "待审核事件" : "全部事件"}</p><h2>{loading ? "正在读取…" : `${ordered.length} 个研究 Case`}</h2></div><span>按下一人工动作与更新时间排序</span></header>{loading ? <ResearchDispatchSkeleton /> : ordered.map((event) => <Link to={eventDeskRoute(event)} key={event.id} className="ros-event-row" aria-label={event.eventTitle}><div><strong>{event.eventTitle}</strong><span>{[event.companyName, event.ticker].filter(Boolean).join(" · ") || "待确认标的"}</span></div><div><i className={eventDeskNeedsHumanReview(event) ? "ros-dot is-review" : "ros-dot"} /><span>{STATUS_LABEL[event.status]} · {event.statusSummary}</span></div><div><b>{eventDeskNeedsHumanReview(event) ? "你需要做" : isPreparationDeskEvent(event) ? "系统正在准备" : workerUnavailable ? "等待执行器" : workerUnknown ? "状态待确认" : "系统正在做"}</b><span>{event.nextHumanAction || (isPreparationDeskEvent(event) ? event.statusSummary : workerUnavailable ? "执行器未启动；已排队工作不会自动推进" : workerUnknown ? "执行器状态待确认" : event.statusSummary)}</span></div><time dateTime={event.updatedAt}>{timeLabel(event.updatedAt)}</time></Link>)}</section></main>;
}
