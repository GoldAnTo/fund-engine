import { researchLabel, researchTime } from "./researchPresentation";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { researchApi, type ResearchItem, type ResearchWorkbenchData } from "@/data/researchApi";
import "./LiveResearchWorkbench.css";
import { ResearchActions } from "./ResearchActions";
import { clearSubmissionKey, submissionKey } from "@/data/researchSubmission";

const errorText = (error: unknown): string => error instanceof Error ? error.message : "研究服务暂时不可用";
function sourceLink(url: string | null): string | undefined {
  if (!url) return undefined;
  try { const parsed = new URL(url); return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : undefined; } catch { return undefined; }
}
const emptyDraft = { title: "", raw: "", question: "", factors: "", actor: "", source: "" };

export function LiveResearchWorkbench() {
  const [items, setItems] = useState<ResearchItem[]>([]);
  const [caseId, setCaseId] = useState(() => new URLSearchParams(window.location.search).get("caseId") ?? "");
  const [data, setData] = useState<ResearchWorkbenchData | null>(null);
  const [query, setQuery] = useState("");
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [creating, setCreating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState("");
  const [draft, setDraft] = useState(emptyDraft);
  const createController = useRef<AbortController>();
  useEffect(() => () => createController.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setListLoading(true); setListError("");
    researchApi.list(controller.signal).then((value) => { if (!controller.signal.aborted) setItems(value.items); }).catch((error) => {
      if (!controller.signal.aborted) { setItems([]); setListError(errorText(error)); }
    }).finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    const restore = () => { setCaseId(new URLSearchParams(window.location.search).get("caseId") ?? ""); setCreating(false); };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setDetailError(""); setDetailLoading(Boolean(caseId));
    if (caseId) researchApi.workbench(caseId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setData(value);
    }).catch((error) => { if (!controller.signal.aborted) { setData(null); setDetailError(errorText(error)); } })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [caseId, refresh]);
  function select(id: string) {
    const url = new URL(window.location.href); url.searchParams.set("caseId", id);
    if (id !== caseId) window.history.pushState({}, "", url);
    setCaseId(id); setCreating(false);
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const factors = draft.factors.split("\n").map((value) => value.trim()).filter(Boolean);
    if (factors.length < 3 || factors.length > 5 || new Set(factors).size !== factors.length) { setCreateError("请填写 3–5 个不重复的候选因素，每行一项。"); return; }
    if (![draft.title, draft.raw, draft.question, draft.actor].every((value) => value.trim())) { setCreateError("请填写标题、原始材料、研究问题和创建人。"); return; }
    const controller = new AbortController(); createController.current = controller;
    setSubmitting(true); setCreateError("");
    try {
      const input = { event_title: draft.title.trim(), raw_input: draft.raw.trim(), research_question: draft.question.trim(), candidate_factors: factors, created_by: draft.actor.trim(), source_type: "pasted_snapshot" as const, research_protocol_required: true, ...(draft.source.trim() ? { source_url: draft.source.trim() } : {}) };
      const key = await submissionKey(input);
      if (controller.signal.aborted) return;
      const created = await researchApi.create(input, controller.signal, key);
      clearSubmissionKey(key);
      if (!controller.signal.aborted) { setDraft(emptyDraft); select(created.case_id); setRefresh((value) => value + 1); }
    } catch (error) { if (!controller.signal.aborted) setCreateError(errorText(error)); }
    finally { if (!controller.signal.aborted) setSubmitting(false); }
  }
  const current = data?.event.case_id === caseId ? data : null;
  const visible = items.filter((item) => item.event_title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className="workbench live-workbench">
    <a className="skip-link" href="#workspace" onClick={() => document.getElementById("workspace")?.focus()}>跳到研究内容</a>
    <header className="workbench-topbar">
      <a className="workbench-brand" href="#workspace"><span>FundClaw</span><i aria-hidden="true" /><small>投资研究 Agent Gateway</small></a>
      <label className="workbench-search"><span className="visually-hidden">搜索研究标题</span><input type="search" placeholder="搜索研究标题" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <div className="live-mode"><span>真实研究空间</span> · <a href="/research">公司研究</a></div>
    </header>
    <nav className="workbench-sidebar" aria-label="研究空间">
      <header className="sidebar-heading"><h2>研究空间</h2><button onClick={() => setCreating(true)} disabled={submitting}>新建研究</button></header>
      <div className="live-list-state">
        {listLoading && <p role="status">正在读取研究列表…</p>}
        {listError && <p role="alert">{listError}</p>}
        <button className="live-secondary" onClick={() => setRefresh((value) => value + 1)} disabled={listLoading}>刷新研究</button>
        {!listLoading && !listError && items.length === 0 && <p>还没有研究。新建研究，记录事件与原始材料。</p>}
        {!listLoading && items.length > 0 && visible.length === 0 && <p>没有匹配的研究。</p>}
      </div>
      <ul className="thread-list">{visible.map((item) => <li key={item.case_id}><button className={caseId === item.case_id && !creating ? "is-active" : ""} aria-current={caseId === item.case_id && !creating ? "page" : undefined} onClick={() => select(item.case_id)} disabled={submitting}><strong>{item.event_title}</strong><span>{item.status_summary || researchLabel(item.lifecycle_status)}</span><small>{researchTime(item.updated_at)}</small></button></li>)}</ul>
    </nav>
    <main className="workbench-main" id="workspace" tabIndex={-1}>
      {creating ? <section className="live-content"><h1>新建事件研究</h1><p>粘贴原始材料，并记录需要验证的问题与候选因素。</p>
        <form className="live-create" onSubmit={create}>
          <fieldset disabled={submitting}>
            <label>事件标题<input required value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
            <label>原始材料<textarea required rows={6} value={draft.raw} onChange={(e) => setDraft({ ...draft, raw: e.target.value })} /></label>
            <label>来源链接（选填）<input type="url" value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })} /></label>
            <label>研究问题<textarea required rows={2} value={draft.question} onChange={(e) => setDraft({ ...draft, question: e.target.value })} /></label>
            <label>候选因素（每行一项，3–5项）<textarea required rows={4} value={draft.factors} onChange={(e) => setDraft({ ...draft, factors: e.target.value })} /></label>
            <label>创建人<input required value={draft.actor} onChange={(e) => setDraft({ ...draft, actor: e.target.value })} /></label>
          </fieldset>
          {createError && <p role="alert">{createError}</p>}
          <div className="live-actions"><button className="live-primary" disabled={submitting}>{submitting ? "正在创建…" : "创建研究"}</button><button type="button" className="live-secondary" disabled={submitting} onClick={() => setCreating(false)}>返回研究</button></div>
        </form></section> : <>
        {detailLoading && <p className="live-content" role="status">正在读取研究内容…</p>}
        {detailError && <div className="live-content"><p role="alert">{detailError}</p><button className="live-secondary" onClick={() => setRefresh((value) => value + 1)}>重试读取</button></div>}
        {!caseId && <section className="live-content"><h1>从事件开始研究</h1><p>选择已有研究查看证据与缺口，或新建研究记录原始材料。</p></section>}
        {current && <>
          <header className="research-header"><div className="research-title"><h1>{current.event.event_title}</h1><span className="task-status">{researchLabel(current.lifecycle.status)}</span></div></header>
          <section className="live-content"><h2>研究状态</h2><p>{current.lifecycle.status_summary}</p><dl className="live-progress"><div><dt>已核验</dt><dd>{current.progress.verified}</dd></div><div><dt>待审核</dt><dd>{current.progress.pending}</dd></div><div><dt>来源无效</dt><dd>{current.progress.invalid_source}</dd></div></dl>
            <h2>当前缺口</h2><p>{current.lifecycle.current_gap || current.progress.current_gap || "尚未记录缺口"}</p>
            <h2>下一步</h2><p>{current.lifecycle.next_human_action || current.next_action.label || "尚未指定下一步"}</p>
            <h2>研究结论</h2><p className="live-metadata">状态：{researchLabel(current.conclusion.state)} · 置信度：{researchLabel(current.conclusion.confidence)}</p><p className="live-verbatim">{current.conclusion.text || "尚未形成结论"}</p>
            <h2>候选因素</h2>{current.factors.length ? <ol className="live-factors">{current.factors.map((factor) => <li key={factor.thesis_id}><h3>{factor.statement}</h3>{factor.description && <p>{factor.description}</p>}<p>支持 {factor.reviewed_support_count} · 反证 {factor.reviewed_contradiction_count} · 待审 {factor.pending_proposal_count}</p>{factor.current_gap && <p>缺口：{factor.current_gap}</p>}</li>)}</ol> : <p>尚未记录候选因素</p>}
            <ResearchActions key={caseId} caseId={caseId} published={current.lifecycle.status === "published"} hasPreparation={Boolean(current.preparation)} onRefresh={() => setRefresh((value) => value + 1)} />
            <p className="live-metadata">研究消息尚未接入此入口。</p>
          </section>
        </>}
      </>}
    </main>
    <aside className="workbench-evidence" aria-label="证据与来源"><section className="live-content"><h2>证据与来源</h2>
      {!creating && current ? <>{current.evidence.length === 0 && <p>本研究尚无可展示证据。</p>}{current.evidence.map((evidence, index) => <article className="live-evidence" key={`${evidence.document_version_id}-${index}`}><h3>{evidence.source_title || "未命名来源"}</h3><p className="live-metadata">{researchLabel(evidence.role)} · {researchLabel(evidence.review_state)}</p><blockquote>{evidence.excerpt}</blockquote><p>{evidence.factor_statement}</p><small>可用时间：{researchTime(evidence.available_at)}</small>{sourceLink(evidence.source_url) ? <a href={sourceLink(evidence.source_url)} target="_blank" rel="noreferrer">查看原始来源 ↗</a> : <p className="live-metadata">未提供可访问的来源链接</p>}</article>)}</> : <p>选择研究后查看对应的证据与原始来源。</p>}
    </section></aside>
  </div>;
}
