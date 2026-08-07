import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventResearchListItem } from "../../domain/eventResearch";

export function EventResearchListScreen() {
  const [searchParams] = useSearchParams();
  const [items, setItems] = useState<EventResearchListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { researchClient.listEventResearch().then(setItems).catch((err: Error) => setError(err.message)); }, []);
  const attentionOnly = searchParams.get("attention") === "1";
  const visibleItems = attentionOnly ? items.filter((item) => ["awaiting_key_review", "draft_ready", "awaiting_scope", "exhausted"].includes(item.status)) : items;
  return <main className="prototype-screen event-list-screen">
    <header className="event-page-header"><div><p className="section-kicker">研究档案</p><h1>{attentionOnly ? "待我处理" : "事件研究"}</h1><p>{attentionOnly ? "这里只保留系统确实需要你判断的事件；其余研究会继续自动运行。" : "每条新闻事件都是独立研究；切换事件不会带入其他事件的证据或结论。"}</p></div><Link className="prototype-button primary" to="/events/new">＋ 创建事件研究</Link></header>
    {error ? <p className="form-error">加载失败：{error}</p> : null}
    <section className="prototype-paper event-table-wrap"><table className="event-table"><thead><tr><th>事件</th><th>系统正在做什么</th><th>最后更新</th><th>你需要做什么</th></tr></thead><tbody>{visibleItems.map((item) => <tr key={item.id}><td><Link to={`/events/${item.id}`}><strong>{item.eventTitle}</strong><small>{[item.companyName, item.ticker].filter(Boolean).join(" · ") || "未确认公司/代码"}</small></Link></td><td><span className={`event-status event-status--${item.status}`}>{item.statusSummary}</span></td><td>{new Date(item.updatedAt).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" })}</td><td>{item.nextHumanAction ? <Link className="event-action-link" to={item.status === "draft_ready" ? `/events/${item.id}/conclusion` : `/events/${item.id}/review`}>{item.nextHumanAction}</Link> : <span className="event-muted">系统继续处理</span>}</td></tr>)}</tbody></table>{visibleItems.length === 0 ? <p className="event-empty-hint">{attentionOnly ? "当前没有需要你处理的事件。" : "还没有事件研究。创建一条新闻事件即可开始。"}</p> : null}</section>
  </main>;
}
