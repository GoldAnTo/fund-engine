import { type FormEvent, type ReactElement, useState } from "react";
import { workbenchEvidence, workbenchFeed, workbenchThreads } from "./mockData";

type FeedFilter = "all" | "summary" | "claim" | "evidence" | "decision";
type EvidenceTab = "evidence" | "questions" | "review";

const filters: Array<{ id: FeedFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "summary", label: "目标" },
  { id: "claim", label: "任务" },
  { id: "evidence", label: "证据" },
  { id: "decision", label: "审核" },
];

const questions = [
  "先进制程设备验证周期是否延长，如何影响收入兑现？",
  "本土清洗设备在高阶产线的份额是否出现拐点？",
  "订单结构改善能否覆盖新增研发与产能投入？",
];

const reviewItems = ["演示审查任务：检查估值假设与时间口径。实际审核尚未执行。"];
const unavailable = { disabled: true, title: "演示模式暂不支持此操作", "aria-describedby": "demo-mode" };

function Icon({ children }: { children: string }): ReactElement {
  return <span className="workbench-icon" aria-hidden="true">{children}</span>;
}

export function ResearchWorkbench(): ReactElement {
  const [activeThreadId, setActiveThreadId] = useState("semiconductor");
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("evidence");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [threadMessages, setThreadMessages] = useState<Record<string, string[]>>({});
  const [query, setQuery] = useState("");
  const [todoOnly, setTodoOnly] = useState(false);
  const draft = drafts[activeThreadId] ?? "";
  const messages = threadMessages[activeThreadId] ?? [];
  const setDraft = (value: string): void => setDrafts((current) => ({ ...current, [activeThreadId]: value }));
  const [expanded, setExpanded] = useState<string[]>(workbenchFeed.map((item) => item.id));
  const [notice, setNotice] = useState("");

  const activeThread = workbenchThreads.find((thread) => thread.id === activeThreadId) ?? {
    id: "semiconductor",
    title: "科创板半导体设备国产化机会研究",
    status: "进行中",
    updatedAt: "更新于 10:24",
  };
  const hasDemo = activeThreadId === "semiconductor";
  const threadFeed = hasDemo ? workbenchFeed : [];
  const evidenceItems = hasDemo ? workbenchEvidence : [];
  const threadQuestions = hasDemo ? questions : [];
  const threadReviews = hasDemo ? reviewItems : [];
  const matchingThreads = workbenchThreads.filter((thread) => thread.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pendingFeed = threadFeed.filter((item) => !todoOnly || item.status === "进行中" || item.status === "待审核");
  function matchesFilter(item: typeof workbenchFeed[number], filter: FeedFilter): boolean {
    if (filter === "all") return true;
    if (filter === "evidence") return item.kind === "agent";
    if (filter === "decision") return item.kind === "reviewer";
    if (filter === "summary") return item.kind === "goal";
    return item.kind !== "goal";
  }
  const visibleFeed = pendingFeed.filter((item) => matchesFilter(item, feedFilter));


  function toggleExpanded(id: string): void {
    setExpanded((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  function sendMessage(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const message = draft.trim();
    if (!message) return;
    setThreadMessages((current) => ({ ...current, [activeThreadId]: [...(current[activeThreadId] ?? []), message] }));
    setDraft("");
    setNotice("已添加本地演示消息，未发送到研究团队");
  }

  return (
    <div className="workbench">
      <a className="skip-link" href="#workspace" onClick={() => document.getElementById("workspace")?.focus()}>跳到研究内容</a>
      <header className="workbench-topbar">
        <a className="workbench-brand" href="#workspace" aria-label="FundClaw 首页">
          <span>FundClaw</span><i aria-hidden="true" /><small>投资研究 Agent Gateway</small>
        </a>
        <label className="workbench-search">
          <Icon>⌕</Icon><span className="visually-hidden">搜索研究线程</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索研究线程标题" />
        </label>
        <div className="workbench-utilities" aria-label="账户操作">
          <button type="button" aria-label="通知" {...unavailable}>♧</button>
          <button type="button" aria-label="帮助" {...unavailable}>?</button>
          <button type="button" className="workbench-profile" {...unavailable}>研 <span>研究员⌄</span></button>
        </div>
      </header>

      <nav className="workbench-sidebar" aria-label="研究空间" id="research-threads" tabIndex={-1}>
        <header className="sidebar-heading"><h2>研究空间</h2><button type="button" {...unavailable}>＋ <span>新建</span></button></header>
        <div className="sidebar-shortcuts">
          <button type="button" {...unavailable}><Icon>♙</Icon>演示研究 <em>{workbenchThreads.length}</em></button>
          <button type="button" {...unavailable}><Icon>♧</Icon>与我协作</button>
        </div>
        <div className="sidebar-section-heading"><h3>研究线程</h3><span aria-hidden="true">⌕　▽</span></div>
        <ul className="thread-list">
          {matchingThreads.map((thread) => {
            const selected = activeThreadId === thread.id;
            return (
              <li key={thread.id} className={selected ? "is-selected" : undefined}>
                <button type="button" aria-current={selected ? "page" : undefined} onClick={() => { setActiveThreadId(thread.id); setNotice("已切换至" + thread.title); }}>
                  <span className={"thread-dot thread-dot--" + (thread.status === "进行中" ? "active" : "idle")} aria-hidden="true" />
                  <strong>{thread.title}</strong>{selected && <span className="thread-pin" aria-hidden="true">⚐</span>}
                  <small>{thread.status} · {thread.updatedAt}</small>
                </button>
              </li>
            );
          })}
        </ul>
        {matchingThreads.length === 0 && <p className="empty-state">没有匹配的研究线程。</p>}
        <section className="decision-list" aria-labelledby="decision-heading">
          <h3 id="decision-heading">关键决策（{hasDemo ? 3 : 0}）</h3>
          {(hasDemo ? ["设备国产化率拐点判断", "北方华创目标价区间", "刻蚀设备进口替代空间"] : []).map((decision, index) => <button type="button" {...unavailable} key={decision}><span>●</span>{decision}<em>待决策</em><small>由 分析师-{index === 0 ? "产业" : index === 1 ? "财务" : "策略"} 提出</small></button>)}
        </section>
        <button className="sidebar-trash" type="button" {...unavailable}><Icon>♜</Icon>回收站</button>
      </nav>

      <main className="workbench-main" aria-label="研究工作台" id="workspace" tabIndex={-1}>
        <p className="demo-banner" id="demo-mode">演示模式：未连接 API，所有研究与证据均为示例。灰色操作暂不可用；消息和草稿仅保存在当前页面，刷新后丢失。</p>
        <header className="research-header">
          <div className="research-title"><button type="button" aria-label="返回研究列表" onClick={() => document.getElementById("research-threads")?.focus()}>‹</button><h1>{activeThread.title}</h1><button type="button" aria-label="收藏当前研究" {...unavailable}>☆</button><span className="status-live">● {activeThread.status}</span><small>{activeThread.updatedAt}</small></div>
          <div className="research-actions"><button type="button" {...unavailable}>⌘ 分享</button><button type="button" {...unavailable}>⇩ 导出</button><button className="primary-action" type="button" {...unavailable}>◌ 新建研究</button></div>
        </header>
        <section className="scope-row" aria-label="研究范围">
          <strong><Icon>⚑</Icon>研究范围（已固定）</strong>
          {hasDemo ? <div className="scope-chips"><span><b>市场：</b>中国大陆（科创板）</span><span><b>主题：</b>半导体设备国产化</span><span><b>时间：</b>2020-01 ～ 2025-05</span><span><b>关注公司：</b>北方华创，中微公司，拓荆科技，华海清科</span></div> : <p className="empty-state">暂无该线程的演示范围。</p>}
        </section>
        <section className="conversation" aria-label="研究对话">
          <div className="view-tabs" role="tablist" aria-label="研究视图">
            <button role="tab" id="conversation-tab" aria-controls="conversation-panel" aria-selected="true" type="button">研究对话</button>
            <button role="tab" tabIndex={-1} aria-selected="false" type="button" {...unavailable}>时间线</button>
            <button role="tab" tabIndex={-1} aria-selected="false" type="button" {...unavailable}>研究笔记</button>
          </div>
          <div className="conversation-tools">
            <div className="feed-filter" aria-label="研究对话筛选">
              {filters.map((filter) => <button key={filter.id} className={feedFilter === filter.id ? "is-selected" : ""} type="button" aria-pressed={feedFilter === filter.id} onClick={() => setFeedFilter(filter.id)}>{filter.label} {pendingFeed.filter((item) => matchesFilter(item, filter.id)).length}</button>)}
            </div>
            <label className="todo-toggle"><input type="checkbox" checked={todoOnly} onChange={(event) => setTodoOnly(event.target.checked)} />仅看待办</label>
            <button type="button" {...unavailable}>⌘ 展开视图</button><button type="button" aria-label="更多视图操作" {...unavailable}>☰</button>
          </div>
          <div className="feed" id="conversation-panel" role="tabpanel" aria-labelledby="conversation-tab" tabIndex={0}>
            {visibleFeed.length === 0 && <p className="empty-state">{hasDemo ? "没有符合筛选条件的演示对话。" : "暂无该线程的演示对话。"}</p>}
            {visibleFeed.map((item) => {
              const open = expanded.includes(item.id);
              return (
                <article className={"feed-item feed-item--" + item.kind} key={item.id}>
                  <time>{["10:24", "10:31", "10:38", "10:45", "10:52"][workbenchFeed.indexOf(item)]}</time>
                  <div className="feed-avatar" aria-hidden="true">{item.kind === "goal" ? "●" : item.kind === "reviewer" ? "◆" : "◒"}</div>
                  <div className="feed-content">
                    <header><h2>{item.title}</h2>{item.status && <span className={"task-status task-status--" + (item.status === "待审核" ? "review" : "running")}>● {item.status}</span>}<button type="button" aria-label={(open ? "收起" : "展开") + item.title} aria-expanded={open} onClick={() => toggleExpanded(item.id)}>{open ? "⌃" : "⌄"}</button></header>
                    {open && <><p>{item.summary}</p>{item.files && <ul className="file-list">{item.files.map((file) => <li key={file.name}><span className={"file-type file-type--" + file.type.toLowerCase()}>{file.type.slice(0, 1)}</span>{file.name}<small>{file.type}</small></li>)}</ul>}{item.references > 0 && <footer>共 {item.references} 个参考 <span>引注 {item.references}</span></footer>}</>}
                  </div>
                </article>
              );
            })}
            {!todoOnly && feedFilter === "all" && messages.map((message, index) => <article className="researcher-message" key={message + index}><span>研究员</span><p>{message}</p></article>)}
          </div>
          <form className="composer" onSubmit={sendMessage}>
            <label className="visually-hidden" htmlFor="research-message">向研究团队发送消息</label>
            <textarea id="research-message" value={draft} onChange={(event) => setDraft(event.target.value)} aria-describedby="demo-mode" placeholder="添加本地演示消息，不会发送给研究团队…" />
            <div><button type="button" aria-label="添加附件" {...unavailable}>♧</button><button type="button" aria-label="添加表格" {...unavailable}>▦</button><button type="button" aria-label="添加任务" {...unavailable}>▣</button><button type="button" aria-label="添加数据图" {...unavailable}>▥</button><span>本地演示</span><button className="composer-send" type="submit" aria-label="发送" disabled={!draft.trim()}>➤</button></div>
          </form>
        </section>
      </main>

      <aside className="workbench-evidence" aria-label="研究证据">
        <div className="evidence-tabs" role="tablist" aria-label="证据面板">
          {(["evidence", "questions", "review"] as const).map((tab, index, tabs) => (
            <button key={tab} id={`tab-${tab}`} role="tab" aria-controls={`panel-${tab}`} aria-selected={evidenceTab === tab} tabIndex={evidenceTab === tab ? 0 : -1} type="button" onClick={() => setEvidenceTab(tab)} onKeyDown={(event) => {
              const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
              if (next === null) return;
              const nextTab = tabs[next];
              if (!nextTab) return;
              event.preventDefault(); setEvidenceTab(nextTab); document.getElementById(`tab-${nextTab}`)?.focus();
            }}>{tab === "evidence" ? "证据与来源" : tab === "questions" ? `待解决问题（${threadQuestions.length}）` : "审查状态"}</button>
          ))}
        </div>
        {evidenceTab === "evidence" && <section className="evidence-panel" role="tabpanel" id="panel-evidence" aria-labelledby="tab-evidence" tabIndex={0}><header><h2>证据与来源</h2><span>示例 {evidenceItems.length} 条，均未核验</span><button type="button" {...unavailable}>按相关性⌄</button></header>{evidenceItems.length === 0 && <p className="empty-state">暂无该线程的演示证据。</p>}{evidenceItems.map((evidence) => <article className={"evidence-card evidence-card--" + evidence.tone} key={evidence.id}><header><span className="evidence-check">{evidence.tone === "counter" ? "!" : evidence.tone === "related" ? "+" : "✓"}</span><h3>{evidence.title}</h3></header><small>{evidence.date} · {evidence.source}</small><p>{evidence.summary}</p><footer><em>{evidence.label}</em><span>示例来源，未核验</span></footer></article>)}<button className="all-evidence" type="button" {...unavailable}>已展示全部示例（{evidenceItems.length}）</button></section>}
        {evidenceTab === "questions" && <section className="side-list" role="tabpanel" id="panel-questions" aria-labelledby="tab-questions" tabIndex={0}><h2>待解决问题</h2>{threadQuestions.length === 0 && <p className="empty-state">暂无该线程的演示问题。</p>}{threadQuestions.map((question, index) => <article key={question}><span>{index + 1}</span><p>{question}</p><small>等待分析师-{index === 0 ? "产业" : index === 1 ? "策略" : "财务"}补充</small></article>)}</section>}
        {evidenceTab === "review" && <section className="side-list" role="tabpanel" id="panel-review" aria-labelledby="tab-review" tabIndex={0}><h2>审查状态</h2>{threadReviews.length === 0 && <p className="empty-state">暂无该线程的演示审查。</p>}{threadReviews.map((item, index) => <article key={item}><span>{index === 0 ? "✓" : "●"}</span><p>{item}</p></article>)}</section>}
      </aside>
      <p className="workbench-notice" role="status">{notice}</p>
    </div>
  );
}
