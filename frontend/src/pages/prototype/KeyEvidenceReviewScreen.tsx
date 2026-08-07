import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventEvidenceCitation } from "../../domain/eventResearch";

export function KeyEvidenceReviewScreen() {
  const { caseId = "" } = useParams();
  const [item, setItem] = useState<EventEvidenceCitation | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => { researchClient.getEventWorkbench(caseId).then((view) => setItem(view.evidence[0] ?? null)); }, [caseId]);
  if (!item) return <main className="prototype-screen"><p>正在准备关键证据…</p></main>;
  return <main className="prototype-screen evidence-review-screen">
    <header className="event-page-header"><div><p className="section-kicker">关键证据审核</p><h1>只审核这一条证据</h1><p>先看原文与定位，再决定它是否足以影响当前因素。</p></div><Link className="prototype-button" to={`/events/${caseId}`}>返回结论工作台</Link></header>
    <article className="prototype-paper evidence-original"><p className="section-kicker">原始证据</p><blockquote>{item.excerpt}</blockquote><dl><div><dt>来源</dt><dd>{item.sourceTitle ?? item.sourceUrl ?? "来源待补充"}</dd></div><div><dt>定位</dt><dd>{JSON.stringify(item.locator)}</dd></div><div><dt>可用时间</dt><dd>{new Date(item.availableAt).toLocaleString("zh-CN")}</dd></div><div><dt>影响因素</dt><dd>{item.factorStatement}</dd></div></dl></article>
    <section className="prototype-paper"><p className="section-kicker">审核决定</p><div className="evidence-review-actions"><button className="prototype-button primary" onClick={() => setMessage("已采纳：该证据会进入已审核证据集。")} type="button">采纳</button><button className="prototype-button" onClick={() => setMessage("已不采纳：该证据不会被用于当前结论。")} type="button">不采纳</button><button className="prototype-button" onClick={() => setMessage("已记录：系统会围绕该缺口继续检索。")} type="button">需要更多材料</button></div>{message ? <p className="event-started">{message}</p> : null}</section>
  </main>;
}
