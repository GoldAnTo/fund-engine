import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import type { EventResearchListItem } from "../../domain/eventResearch";

function timeLabel(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

const STATUS_LABEL: Record<EventResearchListItem["status"], string> = {
  extracting: "正在识别", researching: "系统研究中", awaiting_key_review: "等待证据审核", continuing: "系统继续研究",
  awaiting_scope: "需要调整范围", draft_ready: "等待结论审核", published: "结论已发布", exhausted: "范围已穷尽",
};

export function EventDeskPage() {
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { researchClient.listEventResearch().then(setEvents).catch(() => setError("暂时无法读取事件研究。请检查研究服务后重试。")).finally(() => setLoading(false)); }, []);
  const ordered = useMemo(() => [...events].sort((a, b) => Number(Boolean(b.nextHumanAction)) - Number(Boolean(a.nextHumanAction)) || b.updatedAt.localeCompare(a.updatedAt)), [events]);
  const highest = ordered[0];
  const active = ordered.filter((event) => ["extracting", "researching", "continuing"].includes(event.status));
  const review = ordered.filter((event) => event.nextHumanAction);

  return <main className="ros-page ros-desk">
    <header className="ros-page-head">
      <div><p className="ros-eyebrow">事件研究 · Research Desk</p><h1>今天，先推进哪一个判断？</h1><p>事件是入口，不是信息流。系统按它是否会改变当前判断或已知基金披露暴露排序。</p></div>
      <Link className="ros-button ros-button--primary" to="/events/new">＋ 从事件开始</Link>
    </header>
    {error && <p className="ros-error" role="alert">{error}</p>}
    {!loading && !error && ordered.length === 0 && <section className="ros-empty ros-empty--large"><h2>还没有进行中的研究</h2><p>从公告、研报或新闻快照建立事件 Case；系统会先固定研究范围和可验证因素。</p><Link className="ros-button ros-button--primary" to="/events/new">从事件开始</Link></section>}
    {!!highest && <div className="ros-desk-layout">
      <section className="ros-card ros-case-queue" aria-label="研究优先队列">
        <header className="ros-card-head"><div><p className="ros-eyebrow">优先队列</p><h2>等待推进的判断</h2></div><span>{review.length} 项需要人工决定</span></header>
        <Link className="ros-priority" to={`/events/${highest.id}${highest.nextHumanAction ? "/review" : ""}`}>
          <span className="ros-eyebrow">最高影响</span><h2>{highest.nextHumanAction || highest.statusSummary}</h2><p>{highest.eventTitle} · 完成后决定当前研究是否需要调整范围或复核结论。</p><strong>{highest.nextHumanAction ? "进入审核 →" : "查看 Case →"}</strong>
        </Link>
        <div className="ros-queue-rows">{ordered.slice(1, 6).map((event, index) => <Link className="ros-queue-row" key={event.id} to={`/events/${event.id}`}>
          <span className={`ros-priority-number${event.nextHumanAction ? " is-review" : ""}`}>{index + 2}</span><div><strong>{event.eventTitle}</strong><p>{event.statusSummary}</p></div><span className={event.nextHumanAction ? "ros-need is-review" : "ros-need"}>{event.nextHumanAction || "系统补证中"}</span>
        </Link>)}</div>
      </section>
      <aside className="ros-desk-rail">
        <section><p className="ros-eyebrow">今天的工作</p><h2>先审核，再扩展</h2><p>系统不会自行采纳候选关系，也不会用同期行情自动写成因果结论。</p></section>
        <section><p className="ros-eyebrow">研究网络</p><div className="ros-mini-row"><span>进行中 Case</span><b>{active.length || "—"}</b></div><div className="ros-mini-row"><span>待你审核</span><b>{review.length || "—"}</b></div><div className="ros-mini-row"><span>已发布结论</span><b>{ordered.filter((event) => event.status === "published").length || "—"}</b></div></section>
        <section><p className="ros-eyebrow">系统状态</p><h2>{active.length ? "正在受控运行" : "等待下一项触发"}</h2><p>{active.length ? `${active.length} 个 Case 正在按各自范围、来源许可和配置版本补证。` : "没有后台运行；所有新证据和任务状态都会在这里留下可查看的记录。"}</p></section>
      </aside>
    </div>}
    <section className="ros-all-events"><header><div><p className="ros-eyebrow">全部事件</p><h2>{loading ? "正在读取…" : `${ordered.length} 个研究 Case`}</h2></div><span>按下一人工动作与更新时间排序</span></header>
      {ordered.map((event) => <Link to={`/events/${event.id}`} key={event.id} className="ros-event-row" aria-label={event.eventTitle}>
        <div><strong>{event.eventTitle}</strong><span>{[event.companyName, event.ticker].filter(Boolean).join(" · ") || "待确认标的"}</span></div><div><i className={event.nextHumanAction ? "ros-dot is-review" : "ros-dot"} /><span>{STATUS_LABEL[event.status]} · {event.statusSummary}</span></div><div><b>{event.nextHumanAction ? "你需要做" : "系统正在做"}</b><span>{event.nextHumanAction || event.statusSummary}</span></div><time dateTime={event.updatedAt}>{timeLabel(event.updatedAt)}</time>
      </Link>)}</section>
  </main>;
}
