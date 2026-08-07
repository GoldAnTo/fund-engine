import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventReviewQueue, EventReviewQueueItem } from "../../domain/eventResearch";
import type { ProposalReviewItem } from "../../domain/prototypeTypes";

const sourceStatusLabel = {
  accessible: "可采纳",
  pasted_unverified: "待验证来源",
  invalid: "无效来源",
} as const;

const roleLabel = {
  supports: "支持",
  contradicts: "反驳",
  contextualizes: "补充背景",
} as const;

function displayDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN") : "未记录";
}

function factorLabel(item: EventReviewQueueItem): string {
  return item.thesisStatement || "未归属因素";
}

export function hasOwnKey(object: object, property: PropertyKey): boolean {
  const objectHasOwn = (Object as typeof Object & {
    hasOwn?: (target: object, key: PropertyKey) => boolean;
  }).hasOwn;
  return typeof objectHasOwn === "function"
    ? objectHasOwn(object, property)
    : Object.prototype.hasOwnProperty.call(object, property);
}

function relationshipLabel(aiRole: string | null): string {
  if (aiRole && hasOwnKey(roleLabel, aiRole)) return roleLabel[aiRole as keyof typeof roleLabel];
  return aiRole || "关联";
}

function nextAction(summary: EventReviewQueue["summary"]): string {
  if (summary.pending === 0 && summary.invalidSource > 0) {
    return "没有可采纳证据，系统将继续寻找真实来源";
  }
  return summary.nextAction || "等待下一轮证据审核";
}

export function KeyEvidenceReviewScreen() {
  const { caseId = "" } = useParams();
  const [queue, setQueue] = useState<EventReviewQueue | null>(null);
  const [proposals, setProposals] = useState<ProposalReviewItem[]>([]);
  const [position, setPosition] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextQueue, nextProposals] = await Promise.all([
        researchClient.getEventReviewQueue(caseId),
        researchClient.listReviewProposals(caseId),
      ]);
      setQueue(nextQueue);
      setProposals(nextProposals);
      setPosition((current) => Math.min(current, Math.max(nextQueue.items.length - 1, 0)));
      return nextQueue;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "关键证据队列加载失败，请重试。");
      return null;
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => { void load(); }, [load]);

  const item = queue?.items[position] ?? null;
  const proposal = item ? proposals.find((candidate) => candidate.id === item.proposalId) ?? null : null;
  const sourceIsAcceptable = item?.canAccept === true;
  const canWriteDecision = Boolean(proposal) && !submitting;

  const decide = (outcome: "confirmed" | "rejected", decisionKind: "accepted" | "rejected" | "returned") => {
    if (!item || !proposal || submitting || (outcome === "confirmed" && !item.canAccept)) return;
    setSubmitting(true);
    setMessage("");
    const reason = outcome === "confirmed"
      ? "已核验原始文本、来源定位和可用时间，采纳该证据。"
      : decisionKind === "returned"
        ? "当前材料不能作为正式证据，请继续寻找可验证的真实来源。"
        : "当前材料不足以支撑该因素，不计入结论。";

    void researchClient.reviewProposal(proposal.id, {
      outcome,
      reason,
      expected_version: proposal.version,
      reviewer_id: "event-researcher",
    }).then(async () => {
      const refreshed = await load();
      if (!refreshed) return;
      const remaining = refreshed.summary.pending;
      const following = nextAction(refreshed.summary);
      if (decisionKind === "accepted") {
        setMessage(`已采纳为“${factorLabel(item)}”的正式证据；剩余 ${remaining} 条有效待审。下一步：${following}`);
      } else if (decisionKind === "returned") {
        setMessage(`已退回“${factorLabel(item)}”的材料并继续找真实来源；剩余 ${remaining} 条有效待审。下一步：${following}`);
      } else {
        setMessage(`已不采纳“${factorLabel(item)}”的材料，不计入结论；剩余 ${remaining} 条有效待审。下一步：${following}`);
      }
    }).catch((decisionError) => {
      setMessage(decisionError instanceof Error ? decisionError.message : "审核写入失败，请重试。");
    }).finally(() => setSubmitting(false));
  };

  if (loading && !queue) return <main className="prototype-screen"><p role="status">正在准备关键证据队列…</p></main>;
  if (error && !queue) return <main className="prototype-screen"><p role="alert">{error}</p><button className="prototype-button" onClick={() => void load()} type="button">重新加载</button></main>;
  if (!queue) return null;

  const currentNextAction = nextAction(queue.summary);
  const header = <header className="event-page-header evidence-review-header">
      <div>
        <p className="section-kicker">关键证据审核</p>
        <h1>核验原文，再决定是否计入因素</h1>
        <p>每项决定都保留来源、定位和可用时间，便于复核。</p>
      </div>
      <Link className="prototype-button" to={`/events/${caseId}`}>返回结论工作台</Link>
    </header>;
  const progress = <section aria-label="本轮审核进度" className="evidence-review-progress prototype-paper">
      <span>总数 {queue.summary.total}</span><span>已审核 {queue.summary.reviewed}</span><span>待审核 {queue.summary.pending}</span><span>无效来源 {queue.summary.invalidSource}</span>
      <strong>{queue.items.length ? `当前第 ${position + 1} 条/第 ${queue.summary.currentRound} 轮` : `本轮第 ${queue.summary.currentRound} 轮已完成`}</strong>
      <p>下一步：{currentNextAction}</p>
    </section>;
  const feedback = message ? <p className="event-started" role="status">{message}</p> : null;

  if (queue.items.length === 0) return <main className="prototype-screen evidence-review-screen">
    {header}
    {progress}
    {error ? <p className="evidence-review-alert" role="alert">{error}<button className="prototype-button" onClick={() => void load()} type="button">重新加载</button></p> : null}
    {feedback}
    <section className="prototype-paper evidence-review-empty"><p className="section-kicker">队列已清空</p><h2>本轮没有待审证据</h2><p>系统会在找到可核验的原文后把它加入此处。</p></section>
  </main>;
  if (!item) return null;

  const sourceTitle = item.sourceTitle || "未标明来源标题";

  return <main className="prototype-screen evidence-review-screen">
    {header}
    {progress}

    {error ? <p className="evidence-review-alert" role="alert">{error}<button className="prototype-button" onClick={() => void load()} type="button">重新加载</button></p> : null}
    {feedback}

    <div className="evidence-review-layout">
      <aside aria-label="关键证据队列" className="evidence-review-queue prototype-paper">
        <div className="evidence-review-queue-heading"><div><p className="section-kicker">本轮队列</p><h2>逐条审核</h2></div><span>{queue.items.length} 条</span></div>
        <ol>
          {queue.items.map((queued, index) => <li key={queued.proposalId}>
            <button aria-current={index === position ? "true" : undefined} className={index === position ? "is-selected" : ""} onClick={() => setPosition(index)} type="button">
              <span className="evidence-review-queue-index">{index + 1}</span>
              <span><strong>{factorLabel(queued)}</strong><small>{queued.sourceTitle || "未标明来源标题"}</small><small>AI 关系：{relationshipLabel(queued.aiRole)}</small></span>
              <span className={`evidence-source-status evidence-source-status--${queued.sourceStatus}`}>{sourceStatusLabel[queued.sourceStatus]}</span>
            </button>
          </li>)}
        </ol>
        <div className="evidence-review-navigation">
          <button className="prototype-button" disabled={position === 0} onClick={() => setPosition((current) => current - 1)} type="button">上一条</button>
          <button className="prototype-button" disabled={position === queue.items.length - 1} onClick={() => setPosition((current) => current + 1)} type="button">下一条</button>
        </div>
      </aside>

      <section aria-label="所选证据详情" className="evidence-review-detail">
        <article className="prototype-paper evidence-original">
          <p className="section-kicker">证据原文</p>
          <blockquote>{item.verbatimText || "该来源未提供可审核的冻结原文。"}</blockquote>
          <div className="evidence-review-rationale"><p><strong>{relationshipLabel(item.aiRole)}的因素</strong>{factorLabel(item)}</p><p><strong>AI 理由</strong>{item.aiReason || item.proposalReason || "未提供"}</p></div>
          <dl className="evidence-review-metadata">
            <div><dt>来源标题</dt><dd>{item.documentSourceUrl ? <a href={item.documentSourceUrl} rel="noopener noreferrer" target="_blank">{sourceTitle}</a> : sourceTitle}</dd></div>
            <div><dt>原始 URL</dt><dd>{item.documentSourceUrl ? <a href={item.documentSourceUrl} rel="noopener noreferrer" target="_blank">{item.documentSourceUrl}</a> : "未提供"}</dd></div>
            <div><dt>定位</dt><dd>{Object.keys(item.locator).length ? JSON.stringify(item.locator) : "未提供"}</dd></div>
            <div><dt>发布日期</dt><dd>{displayDate(item.documentPublishedAt)}</dd></div>
            <div><dt>可用时间</dt><dd>{displayDate(item.availableAt)}</dd></div>
            <div><dt>来源状态</dt><dd><span className={`evidence-source-status evidence-source-status--${item.sourceStatus}`}>{sourceStatusLabel[item.sourceStatus]}</span><span>{item.sourceStatusReason}</span></dd></div>
          </dl>
        </article>

        <section className="prototype-paper evidence-review-decision">
          <p className="section-kicker">审核决定</p>
          <h2>{factorLabel(item)}</h2>
          {!sourceIsAcceptable ? <div className="evidence-review-nonaccept"><p>来源不可采纳：{item.sourceStatusReason}</p><p>此来源仅供审计，不能计入结论。</p></div> : null}
          {!proposal ? <p className="event-empty-hint">当前队列项没有可写入的待审提议；可查看来源审计记录或返回工作台。</p> : null}
          <div className="evidence-review-actions">
            <button className="prototype-button primary" disabled={!canWriteDecision || !sourceIsAcceptable} onClick={() => decide("confirmed", "accepted")} type="button">采纳为本因素的正式证据</button>
            <button className="prototype-button" disabled={!canWriteDecision} onClick={() => decide("rejected", "rejected")} type="button">不采纳，不计入结论</button>
            <button className="prototype-button" disabled={!canWriteDecision} onClick={() => decide("rejected", "returned")} type="button">退回并继续找真实来源</button>
          </div>
        </section>
      </section>
    </div>
  </main>;
}
