import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventExtraction } from "../../domain/eventResearch";

const EMPTY: EventExtraction = {
  eventTitle: null, companyName: null, ticker: null, eventAt: null,
  marketReaction: null, summary: null, researchQuestion: "", candidateFactors: [], confirmationRequired: true,
};

export function EventResearchCreateScreen() {
  const navigate = useNavigate();
  const [rawInput, setRawInput] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [draft, setDraft] = useState<EventExtraction>(EMPTY);
  const [extracting, setExtracting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function extract() {
    setExtracting(true); setError(null);
    try { setDraft(await researchClient.extractEventResearch({ rawInput, sourceUrl })); }
    catch (err) { setError(err instanceof Error ? err.message : "AI 提取失败，请稍后重试。"); }
    finally { setExtracting(false); }
  }
  async function create() {
    if (!draft.eventTitle || draft.candidateFactors.length < 3) return;
    setCreating(true); setError(null);
    try {
      const created = await researchClient.createEventResearch({ ...draft, rawInput, sourceUrl, eventTitle: draft.eventTitle, createdBy: "researcher" });
      setStarted(true);
      window.setTimeout(() => navigate(`/events/${created.caseId}`), 650);
    } catch (err) { setError(err instanceof Error ? err.message : "创建失败，请检查信息后重试。"); }
    finally { setCreating(false); }
  }
  const ready = Boolean(draft.eventTitle && draft.researchQuestion && draft.candidateFactors.length >= 3);

  return <main className="prototype-screen event-create-screen">
    <header className="event-page-header">
      <div><p className="section-kicker">事件研究</p><h1>从一条新闻开始研究</h1><p>粘贴新闻或你的观察，系统提取事实并提出可修改的问题与待验证因素。</p></div>
    </header>
    {started ? <section className="event-started" role="status"><strong>系统正在自动研究</strong><span>已建立第一轮证据检索；有关键材料时才会请你审核。</span></section> : null}
    <section className="event-create-grid">
      <div className="prototype-paper event-raw-input">
        <label>新闻或研究信息<textarea aria-label="新闻或研究信息" rows={12} value={rawInput} onChange={(e) => setRawInput(e.target.value)} placeholder="粘贴新闻、公告摘要，或写下你想研究的市场现象…" /></label>
        <label>来源链接（可选）<input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://…" /></label>
        <button type="button" className="prototype-button primary" disabled={!rawInput.trim() || extracting} onClick={extract}>{extracting ? "正在提取…" : "AI 提取关键信息"}</button>
      </div>
      <div className="prototype-paper event-confirmation">
        <p className="section-kicker">需要人工确认</p><h2>确认研究起点</h2>
        {!draft.eventTitle ? <p className="event-empty-hint">先粘贴信息并提取。无法从原文确认的事实会保持空白，不会被补写。</p> : <>
          <label>事件标题<input value={draft.eventTitle} onChange={(e) => setDraft((p) => ({ ...p, eventTitle: e.target.value }))} /></label>
          <div className="event-inline-fields"><label>公司<input value={draft.companyName ?? ""} onChange={(e) => setDraft((p) => ({ ...p, companyName: e.target.value || null }))} /></label><label>代码<input value={draft.ticker ?? ""} onChange={(e) => setDraft((p) => ({ ...p, ticker: e.target.value || null }))} /></label></div>
          <label>研究问题<textarea aria-label="研究问题" rows={3} value={draft.researchQuestion} onChange={(e) => setDraft((p) => ({ ...p, researchQuestion: e.target.value }))} /></label>
          <fieldset><legend>待验证因素（保留 3–5 项）</legend>{draft.candidateFactors.map((factor, index) => <label key={index} className="event-factor"><input type="checkbox" checked onChange={() => setDraft((p) => ({ ...p, candidateFactors: p.candidateFactors.filter((_, i) => i !== index) }))} /><input value={factor} onChange={(e) => setDraft((p) => ({ ...p, candidateFactors: p.candidateFactors.map((item, i) => i === index ? e.target.value : item) }))} /></label>)}</fieldset>
          <button type="button" className="prototype-button primary" disabled={!ready || creating} onClick={create}>{creating ? "正在创建…" : "创建并开始自动研究"}</button>
        </>}
        {error ? <p className="form-error">{error}</p> : null}
      </div>
    </section>
  </main>;
}
