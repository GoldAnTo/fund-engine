import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventWorkbench } from "../../domain/eventResearch";

export function EventConclusionReviewScreen() {
  const { caseId = "" } = useParams();
  const [view, setView] = useState<EventWorkbench | null>(null);
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void researchClient.getEventWorkbench(caseId).then((result) => {
      setView(result);
      setText(result.conclusion.text);
    });
  }, [caseId]);

  const publish = () => {
    if (!text.trim() || submitting) return;
    setSubmitting(true);
    void researchClient.publishEventConclusion({ caseId, text: text.trim(), reviewer: "event-researcher" })
      .then(() => setMessage("已发布正式结论。该版本保留了草稿和当时的证据快照，可随时复核。"))
      .catch((error: Error) => setMessage(error.message || "发布失败，请重试。"))
      .finally(() => setSubmitting(false));
  };

  if (!view) return <main className="prototype-screen"><p>正在准备结论草案…</p></main>;
  return <main className="prototype-screen evidence-review-screen">
    <header className="event-page-header"><div><p className="section-kicker">结论复核</p><h1>确认这项研究能说什么</h1><p>结论只能基于已审核材料；可以编辑措辞，但不能隐藏证据缺口。</p></div><Link className="prototype-button" to={`/events/${caseId}`}>返回研究工作台</Link></header>
    <section className="prototype-paper"><p className="section-kicker">AI 草案（可编辑）</p><label htmlFor="event-conclusion-text">正式结论</label><textarea id="event-conclusion-text" value={text} onChange={(event) => setText(event.target.value)} rows={6} /><p className="event-muted">当前状态：{view.conclusion.state === "ai_draft" ? "待人工确认" : "已发布"}</p></section>
    <section className="prototype-paper"><p className="section-kicker">结论依据</p>{view.conclusion.citations.length ? <ul className="event-citation-list">{view.conclusion.citations.map((citation, index) => <li key={`${citation.sourceUrl}-${index}`}><strong>{citation.factorStatement}</strong><span>{citation.excerpt}</span><small>{citation.sourceTitle ?? citation.sourceUrl ?? "来源待补充"} · 定位 {JSON.stringify(citation.locator)}</small></li>)}</ul> : <p>暂无已审核证据。请保留“不能给出因果结论”的限定，或返回继续补证。</p>}</section>
    <section className="prototype-paper evidence-review-actions"><button className="prototype-button primary" disabled={!text.trim() || submitting || view.conclusion.state === "published"} onClick={publish} type="button">确认并发布结论</button>{message ? <p className="event-started">{message}</p> : null}</section>
  </main>;
}
