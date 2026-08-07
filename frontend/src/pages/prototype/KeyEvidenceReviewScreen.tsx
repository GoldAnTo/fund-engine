import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventEvidenceCitation } from "../../domain/eventResearch";
import type { ProposalReviewItem, ReviewQueueViewItem } from "../../domain/prototypeTypes";

function citationFromQueue(item: ReviewQueueViewItem): EventEvidenceCitation {
  return {
    caseId: item.caseId,
    factorStatement: item.thesisStatement || item.statementText,
    role: item.aiRole,
    reviewState: "machine_generated",
    sourceTitle: item.documentSourceUrl || null,
    sourceUrl: item.documentSourceUrl || null,
    excerpt: item.verbatimText || item.statementText,
    locator: item.locator,
    availableAt: item.availableAt,
  };
}

export function KeyEvidenceReviewScreen() {
  const { caseId = "" } = useParams();
  const [item, setItem] = useState<EventEvidenceCitation | null>(null);
  const [proposal, setProposal] = useState<ProposalReviewItem | null>(null);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(() => Promise.all([
    researchClient.getEventWorkbench(caseId),
    researchClient.listReviewProposals(caseId),
    researchClient.getReviewQueueView(caseId),
  ]).then(([view, proposals, queue]) => {
    const pending = proposals.find((candidate) => queue.items.some((queued) => queued.linkId === candidate.id)) ?? proposals[0] ?? null;
    const queued = pending ? queue.items.find((candidate) => candidate.linkId === pending.id) : queue.items[0];
    setProposal(pending);
    setItem(queued ? citationFromQueue(queued) : view.evidence[0] ?? null);
  }), [caseId]);

  useEffect(() => { void load(); }, [load]);

  const decide = (outcome: "confirmed" | "rejected", completedMessage: string) => {
    if (!proposal || submitting) return;
    setSubmitting(true);
    setMessage("");
    void researchClient.reviewProposal(proposal.id, {
      outcome,
      reason: outcome === "rejected" ? "当前材料不足以支撑该因素，请系统继续补充可核验原始证据。" : "已核验原始文本、来源定位和可用时间，采纳该证据。",
      expected_version: proposal.version,
      reviewer_id: "event-researcher",
    }).then(() => {
      setMessage(completedMessage);
      return load();
    }).catch((error: Error) => {
      setMessage(error.message || "审核写入失败，请重试。");
    }).finally(() => setSubmitting(false));
  };

  if (!item) return <main className="prototype-screen"><p>正在准备关键证据…</p></main>;
  return <main className="prototype-screen evidence-review-screen">
    <header className="event-page-header"><div><p className="section-kicker">关键证据审核</p><h1>只审核这一条证据</h1><p>先看原文与定位，再决定它是否足以影响当前因素。</p></div><Link className="prototype-button" to={`/events/${caseId}`}>返回结论工作台</Link></header>
    <article className="prototype-paper evidence-original"><p className="section-kicker">原始证据</p><blockquote>{item.excerpt}</blockquote><dl><div><dt>来源</dt><dd>{item.sourceTitle ?? item.sourceUrl ?? "来源待补充"}</dd></div><div><dt>定位</dt><dd>{JSON.stringify(item.locator)}</dd></div><div><dt>可用时间</dt><dd>{new Date(item.availableAt).toLocaleString("zh-CN")}</dd></div><div><dt>影响因素</dt><dd>{item.factorStatement}</dd></div></dl></article>
    <section className="prototype-paper"><p className="section-kicker">审核决定</p><p>这项决定会写入研究台账；所有待审关键证据处理完后，系统将自动继续研究或生成结论草稿。</p><div className="evidence-review-actions"><button className="prototype-button primary" disabled={!proposal || submitting} onClick={() => decide("confirmed", "已采纳：该证据已进入已审核证据集，系统会继续处理其余关键项。")} type="button">采纳</button><button className="prototype-button" disabled={!proposal || submitting} onClick={() => decide("rejected", "已不采纳：该证据不会用于当前结论。")} type="button">不采纳</button><button className="prototype-button" disabled={!proposal || submitting} onClick={() => decide("rejected", "已记录：系统会围绕该缺口继续检索。")} type="button">需要更多材料</button></div>{!proposal ? <p className="event-started">当前没有可写入的待审提议；请返回工作台查看系统进度。</p> : null}{message ? <p className="event-started">{message}</p> : null}</section>
  </main>;
}
