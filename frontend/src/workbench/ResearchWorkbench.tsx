import { type FormEvent, type ReactElement, useMemo, useState } from "react";
import { workbenchEvidence, workbenchFeed, workbenchThreads } from "./mockData";

type FeedFilter = "all" | "summary" | "claim" | "evidence" | "decision";
type EvidenceTab = "evidence" | "questions" | "review";

const filters: Array<{ id: FeedFilter; label: string }> = [
  { id: "all", label: "全部 18" },
  { id: "summary", label: "结论 4" },
  { id: "claim", label: "求证 5" },
  { id: "evidence", label: "证据 6" },
  { id: "decision", label: "决策 3" },
];

const questions = [
  "先进制程设备验证周期是否延长，如何影响收入兑现？",
  "本土清洗设备在高阶产线的份额是否出现拐点？",
  "订单结构改善能否覆盖新增研发与产能投入？",
];

const reviewItems = [
  "12 条证据已验证，6 条待复核，3 条反证据仍待比对。",
  "质控审核员正在检查估值假设与时间口径。",
  "下一轮人工审核预计在 13:00 前完成。",
];

function Icon({ children }: { children: string }): ReactElement {
  return <span className="workbench-icon" aria-hidden="true">{children}</span>;
}

export function ResearchWorkbench(): ReactElement {
  const [activeThreadId, setActiveThreadId] = useState("semiconductor");
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("evidence");
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string[]>(workbenchFeed.map((item) => item.id));
  const [notice, setNotice] = useState("");

  const activeThread = workbenchThreads.find((thread) => thread.id === activeThreadId) ?? {
    id: "semiconductor",
    title: "科创板半导体设备国产化机会研究",
    status: "进行中",
    updatedAt: "更新于 10:24",
  };
  const visibleFeed = useMemo(() => {
    if (feedFilter === "all") return workbenchFeed;
    if (feedFilter === "evidence") return workbenchFeed.filter((item) => item.kind === "agent");
    if (feedFilter === "decision") return workbenchFeed.filter((item) => item.kind === "reviewer");
    if (feedFilter === "summary") return workbenchFeed.filter((item) => item.kind === "goal");
    return workbenchFeed.filter((item) => item.kind !== "goal");
  }, [feedFilter]);

  function toggleExpanded(id: string): void {
    setExpanded((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  function sendMessage(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const message = draft.trim();
    if (!message) return;
    setMessages((current) => [...current, message]);
    setDraft("");
    setNotice("已添加到研究对话");
  }

  function actionFeedback(message: string): void {
    setNotice(message);
  }

  return (
    <div className="workbench">
      <a className="skip-link" href="#workspace">跳到研究内容</a>
      <header className="workbench-topbar">
        <a className="workbench-brand" href="#workspace" aria-label="FundClaw 首页">
          <span>FundClaw</span><i aria-hidden="true" /><small>投资研究 Agent Gateway</small>
        </a>
        <label className="workbench-search">
          <Icon>⌕</Icon><span className="visually-hidden">搜索研究内容</span>
          <input type="search" placeholder="搜索研究内容、文档、主题" />
        </label>
        <div className="workbench-utilities" aria-label="账户操作">
          <button type="button" aria-label="通知" onClick={() => actionFeedback("没有新的通知")}>♧<b>3</b></button>
          <button type="button" aria-label="帮助" onClick={() => actionFeedback("帮助中心已准备好")}>?</button>
          <button type="button" className="workbench-profile" onClick={() => actionFeedback("研究员账户菜单")}>研 <span>研究员⌄</span></button>
        </div>
      </header>

      <nav className="workbench-sidebar" aria-label="研究空间">
        <header className="sidebar-heading"><h2>研究空间</h2><button type="button" onClick={() => actionFeedback("已创建新的 mock 研究")}>＋ <span>新建</span></button></header>
        <div className="sidebar-shortcuts">
          <button type="button"><Icon>♙</Icon>我的研究 <em>12</em></button>
          <button type="button"><Icon>♧</Icon>与我协作 <em>3</em></button>
        </div>
        <div className="sidebar-section-heading"><h3>研究线程</h3><span aria-hidden="true">⌕　▽</span></div>
        <ul className="thread-list">
          {workbenchThreads.map((thread) => {
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
        <section className="decision-list" aria-labelledby="decision-heading">
          <h3 id="decision-heading">关键决策（3）</h3>
          {["设备国产化率拐点判断", "北方华创目标价区间", "刻蚀设备进口替代空间"].map((decision, index) => <button type="button" key={decision}><span>●</span>{decision}<em>待决策</em><small>由 分析师-{index === 0 ? "产业" : index === 1 ? "财务" : "策略"} 提出</small></button>)}
        </section>
        <button className="sidebar-trash" type="button"><Icon>♜</Icon>回收站</button>
      </nav>

      <main className="workbench-main" aria-label="研究工作台" id="workspace">
        <header className="research-header">
          <div className="research-title"><button type="button" aria-label="返回研究列表" onClick={() => actionFeedback("研究列表已聚焦")}>‹</button><h1>{activeThread.title}</h1><button type="button" aria-label="收藏当前研究">☆</button><span className="status-live">● 进行中</span><small>更新于 10:24</small></div>
          <div className="research-actions"><button type="button" onClick={() => actionFeedback("分享链接已复制")}>⌘ 分享</button><button type="button" onClick={() => actionFeedback("导出任务已加入队列")}>⇩ 导出</button><button className="primary-action" type="button" onClick={() => actionFeedback("新研究已建立")}>◌ 新建研究</button></div>
        </header>
        <section className="scope-row" aria-label="研究范围">
          <strong><Icon>⚑</Icon>研究范围（已固定）</strong>
          <div className="scope-chips"><span><b>市场：</b>中国大陆（科创板）</span><span><b>主题：</b>半导体设备国产化</span><span><b>时间：</b>2020-01 ～ 2025-05</span><span><b>关注公司：</b>北方华创，中微公司，拓荆科技，华海清科　⌕ 调整范围</span></div>
        </section>
        <section className="conversation" aria-label="研究对话">
          <div className="view-tabs" role="tablist" aria-label="研究视图">
            <button role="tab" aria-selected="true" type="button">研究对话</button>
            <button role="tab" aria-selected="false" type="button" onClick={() => actionFeedback("时间线视图正在准备")}>时间线</button>
            <button role="tab" aria-selected="false" type="button" onClick={() => actionFeedback("研究笔记视图正在准备")}>研究笔记</button>
          </div>
          <div className="conversation-tools">
            <div className="feed-filter" aria-label="研究对话筛选">
              {filters.map((filter) => <button key={filter.id} className={feedFilter === filter.id ? "is-selected" : ""} type="button" onClick={() => setFeedFilter(filter.id)}>{filter.label}</button>)}
            </div>
            <label className="todo-toggle"><input type="checkbox" onChange={(event) => actionFeedback(event.target.checked ? "已仅显示待办项" : "已显示全部项")} />仅看待办</label>
            <button type="button" onClick={() => actionFeedback("当前为展开视图")}>⌘ 展开视图</button><button type="button" aria-label="更多视图操作">☰</button>
          </div>
          <div className="feed" aria-live="polite">
            {visibleFeed.map((item, index) => {
              const open = expanded.includes(item.id);
              return (
                <article className={"feed-item feed-item--" + item.kind} key={item.id}>
                  <time>{index === 0 ? "10:24" : index === 1 ? "10:31" : index === 2 ? "10:38" : "10:45"}</time>
                  <div className="feed-avatar" aria-hidden="true">{item.kind === "goal" ? "●" : item.kind === "reviewer" ? "◆" : "◒"}</div>
                  <div className="feed-content">
                    <header><h2>{item.title}</h2>{item.status && <span className={"task-status task-status--" + (item.status === "待审核" ? "review" : "running")}>● {item.status}</span>}<button type="button" aria-label={(open ? "收起" : "展开") + item.title} onClick={() => toggleExpanded(item.id)}>{open ? "⌃" : "⌄"}</button></header>
                    {open && <><p>{item.summary}</p>{item.files && <ul className="file-list">{item.files.map((file) => <li key={file.name}><span className={"file-type file-type--" + file.type.toLowerCase()}>{file.type.slice(0, 1)}</span>{file.name}<small>{file.type}</small></li>)}</ul>}{item.references > 0 && <footer>共 {item.references} 个参考 <span>引注 {item.references}</span></footer>}</>}
                  </div>
                </article>
              );
            })}
            {messages.map((message, index) => <article className="researcher-message" key={message + index}><span>研究员</span><p>{message}</p></article>)}
          </div>
          <form className="composer" onSubmit={sendMessage}>
            <label className="visually-hidden" htmlFor="research-message">向研究团队发送消息</label>
            <textarea id="research-message" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="向研究团队发送消息或提出研究请求…" />
            <div><button type="button" aria-label="添加附件">♧</button><button type="button" aria-label="添加表格">▦</button><button type="button" aria-label="添加任务">▣</button><button type="button" aria-label="添加数据图">▥</button><span>发送给　研究团队⌄</span><button className="composer-send" type="submit" aria-label="发送">➤</button></div>
          </form>
        </section>
      </main>

      <aside className="workbench-evidence" aria-label="研究证据">
        <div className="evidence-tabs" role="tablist" aria-label="证据面板">
          <button role="tab" aria-selected={evidenceTab === "evidence"} type="button" onClick={() => setEvidenceTab("evidence")}>证据与来源</button>
          <button role="tab" aria-selected={evidenceTab === "questions"} type="button" onClick={() => setEvidenceTab("questions")}>待解决问题（3）</button>
          <button role="tab" aria-selected={evidenceTab === "review"} type="button" onClick={() => setEvidenceTab("review")}>审查状态</button>
        </div>
        {evidenceTab === "evidence" && <section className="evidence-panel" role="tabpanel" aria-label="证据与来源"><header><h2>证据与来源</h2><span>已验证 12　待验证 6　反证据 3</span><button type="button">按相关性⌄</button></header>{workbenchEvidence.map((evidence) => <article className={"evidence-card evidence-card--" + evidence.tone} key={evidence.id}><header><span className="evidence-check">{evidence.tone === "counter" ? "!" : evidence.tone === "related" ? "+" : "✓"}</span><h3>{evidence.title}</h3></header><small>{evidence.date} · {evidence.source}</small><p>{evidence.summary}</p><footer><em>{evidence.label}</em><span>引注 {evidence.id === "semi" ? 5 : evidence.id === "asml" ? 3 : 2}　↗</span></footer></article>)}<button className="all-evidence" type="button">查看全部证据（21）</button></section>}
        {evidenceTab === "questions" && <section className="side-list" role="tabpanel" aria-label="待解决问题"><h2>待解决问题</h2>{questions.map((question, index) => <article key={question}><span>{index + 1}</span><p>{question}</p><small>等待分析师-{index === 0 ? "产业" : index === 1 ? "策略" : "财务"}补充</small></article>)}</section>}
        {evidenceTab === "review" && <section className="side-list" role="tabpanel" aria-label="审查状态"><h2>审查状态</h2>{reviewItems.map((item, index) => <article key={item}><span>{index === 0 ? "✓" : "●"}</span><p>{item}</p></article>)}</section>}
      </aside>
      <p className="workbench-notice" role="status">{notice}</p>
    </div>
  );
}
