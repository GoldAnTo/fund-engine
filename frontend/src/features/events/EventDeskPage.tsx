import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import type { EventResearchListItem } from "../../domain/eventResearch";

function timeLabel(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
    .format(new Date(value));
}

const STATUS_LABEL: Record<EventResearchListItem["status"], string> = {
  extracting: "正在识别",
  researching: "系统研究中",
  awaiting_key_review: "等待证据审核",
  continuing: "系统继续研究",
  awaiting_scope: "需要调整范围",
  draft_ready: "等待结论审核",
  published: "结论已发布",
  exhausted: "范围已穷尽",
};

export function EventDeskPage() {
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    researchClient.listEventResearch()
      .then(setEvents)
      .catch(() => setError("暂时无法读取事件研究。请检查研究服务后重试。"))
      .finally(() => setLoading(false));
  }, []);

  const ordered = useMemo(
    () => [...events].sort((a, b) => Number(Boolean(b.nextHumanAction)) - Number(Boolean(a.nextHumanAction)) || b.updatedAt.localeCompare(a.updatedAt)),
    [events],
  );
  const attention = ordered.filter((event) => event.nextHumanAction);

  return (
    <main className="ros-page event-desk">
      <section className="ros-page__heading">
        <div>
          <p className="ros-eyebrow">研究入口 · 事件优先</p>
          <h1>事件研究</h1>
          <p className="ros-lede">先区分系统正在验证什么，再明确你需要作出的审核判断。</p>
        </div>
        <Link className="ros-button ros-button--primary" to="/events/new">新建事件研究 <span aria-hidden>→</span></Link>
      </section>

      {attention.length > 0 && (
        <section className="ros-attention" aria-label="待你处理">
          <div className="ros-attention__heading"><span className="ros-count">{attention.length}</span><h2>待你处理</h2><span>这些判断不会由系统自动代替。</span></div>
          <div className="ros-attention__items">
            {attention.slice(0, 3).map((event) => (
              <Link className="ros-attention__item" to={`/events/${event.id}/review`} key={event.id}>
                <span className="ros-status-dot ros-status-dot--human" aria-hidden />
                <span><strong>{event.eventTitle}</strong><small>{event.nextHumanAction}</small></span>
                <span aria-hidden>→</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="ros-list-section" aria-labelledby="event-list-title">
        <div className="ros-section-heading"><div><p className="ros-eyebrow">全部事件</p><h2 id="event-list-title">{loading ? "正在读取…" : `${ordered.length} 个研究 Case`}</h2></div><span className="ros-muted">按下一人工动作与更新时间排序</span></div>
        {error && <p className="ros-error" role="alert">{error}</p>}
        {!loading && !error && ordered.length === 0 && <div className="ros-empty">尚无事件研究。把一条新闻、公告或研报片段放进来，先固定问题与因素。</div>}
        <div className="ros-event-table">
          {ordered.map((event) => (
            <Link to={`/events/${event.id}`} key={event.id} className="ros-event-row" aria-label={event.eventTitle}>
              <div className="ros-event-row__title"><strong>{event.eventTitle}</strong><span>{[event.companyName, event.ticker].filter(Boolean).join(" · ") || "待确认标的"}</span></div>
              <div className="ros-event-row__system"><span className={`ros-pill ros-pill--${event.nextHumanAction ? "human" : "system"}`}>{STATUS_LABEL[event.status]}</span><span>{event.statusSummary}</span></div>
              <div className="ros-event-row__action">{event.nextHumanAction ? <><strong>你需要做</strong><span>{event.nextHumanAction}</span></> : <><strong>系统正在做</strong><span>{event.statusSummary}</span></>}</div>
              <time dateTime={event.updatedAt}>{timeLabel(event.updatedAt)}</time>
            </Link>
          ))}
        </div>
      </section>
    </main>
  );
}
