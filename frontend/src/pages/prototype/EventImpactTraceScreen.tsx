import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  EventImpactFactor,
  EventImpactFund,
  EventImpactRelation,
  EventImpactTrace,
  EventWorkbench,
} from "../../domain/eventResearch";

type ReviewOutcome = "accepted" | "rejected" | "needs_more";

function safeExternalHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function relationStatus(relation: EventImpactRelation) {
  if (relation.review?.outcome === "accepted") return "人工已接受，原关系记录保持不变";
  if (relation.review?.outcome === "rejected") return "人工已拒绝";
  if (relation.sourceStatementId) return "关系来源已关联";
  return "缺少可采纳的关系来源";
}

function isReviewable(relation: EventImpactRelation) {
  return relation.isHighImpact && relation.isReviewable && relation.effectiveStatus === "candidate";
}

function ReviewDialog({
  relation,
  onClose,
  onSubmit,
  submitting,
  error,
}: {
  relation: EventImpactRelation;
  onClose: () => void;
  onSubmit: (outcome: ReviewOutcome, reason: string, reviewer: string) => void;
  submitting: boolean;
  error: string | null;
}) {
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    dialogRef.current?.querySelector<HTMLSelectElement>("select")?.focus();
  }, []);

  const keepFocusInside = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]), select:not([disabled]), textarea:not([disabled]), input:not([disabled])",
      ) ?? [],
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    onSubmit(
      data.get("outcome") as ReviewOutcome,
      String(data.get("reason") ?? ""),
      String(data.get("reviewer") ?? ""),
    );
  };

  return (
    <div className="impact-review-backdrop">
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="impact-review-title"
        className="impact-review-dialog prototype-paper"
        onKeyDown={keepFocusInside}
      >
        <div className="impact-review-dialog__heading">
          <div>
            <p className="section-kicker">人工复核</p>
            <h2 id="impact-review-title">审核公司传导关系</h2>
            <p>{relation.companyName}，{relation.mechanism}</p>
          </div>
          <button className="prototype-button" type="button" onClick={onClose}>
            取消
          </button>
        </div>
        <form className="impact-review-form" onSubmit={submit}>
          <label>
            审核结果
            <select name="outcome" aria-label="审核结果" defaultValue="needs_more">
              <option value="accepted">接受为候选关系</option>
              <option value="rejected">拒绝该关系</option>
              <option value="needs_more">仍需补充来源</option>
            </select>
          </label>
          <label>
            审核说明
            <textarea name="reason" aria-label="审核说明" required rows={4} />
          </label>
          <label>
            审核人
            <input name="reviewer" aria-label="审核人" required />
          </label>
          {error ? <p className="impact-review-form__error" role="alert">提交失败：{error}</p> : null}
          <div className="impact-review-form__actions">
            <button className="prototype-button" type="button" onClick={onClose} disabled={submitting}>
              返回
            </button>
            <button className="prototype-button primary" type="submit" disabled={submitting}>
              {submitting ? "正在记录…" : "提交审核"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function FactorRelations({ factor, onReview }: { factor: EventImpactFactor; onReview: (relation: EventImpactRelation) => void }) {
  return (
    <article className="impact-factor" data-classification={factor.classification}>
      <div className="impact-factor__summary">
        <p className="section-kicker">因素 {factor.rank} · {factor.classification}</p>
        <h3>{factor.statement}</h3>
        <p>{factor.explanation}</p>
      </div>
      <div className="impact-relations">
        {factor.relations.map((relation) => (
          <article className="impact-relation" key={relation.relationId}>
            <div className="impact-relation__heading">
              <div>
                <h4>{relation.companyName}</h4>
                <p>{relation.relationKind} · {relation.direction} · {relation.mechanism}</p>
              </div>
              <span className={`impact-status impact-status--${relation.effectiveStatus}`}>
                {relation.effectiveStatus === "verified" ? "已核验" : relation.effectiveStatus === "rejected" ? "已拒绝" : "待核验"}
              </span>
            </div>
            <p className="impact-relation__boundary">{relationStatus(relation)}</p>
            {relation.companyType === "unlisted_supplier" || relation.companyType !== "listed" ? (
              <p className="impact-relation__unlisted">未上市主体不映射股票或基金敞口。</p>
            ) : (
              <p className="impact-relation__stock">A 股映射：{relation.stocks.map((stock) => `${stock.name}（${stock.code}）`).join("、") || "尚未解析"}</p>
            )}
            {isReviewable(relation) ? (
              <button className="prototype-button impact-relation__review" type="button" onClick={() => onReview(relation)}>
                审核此关系
              </button>
            ) : null}
          </article>
        ))}
        {!factor.relations.length ? <p className="impact-empty">当前因素尚未解析出公司关系。</p> : null}
      </div>
    </article>
  );
}

function EvidenceLayer({ trace }: { trace: EventImpactTrace }) {
  const relations = trace.factors.flatMap((factor) => factor.relations);
  return (
    <section className="impact-layer impact-evidence" aria-labelledby="impact-evidence-title">
      <div className="impact-layer__heading">
        <div>
          <p className="section-kicker">第三层</p>
          <h2 id="impact-evidence-title">证据与边界</h2>
        </div>
      </div>
      <div className="impact-evidence-list">
        {relations.map((relation) => (
          <article key={relation.relationId} className="impact-evidence-row">
            <strong>{relation.companyName}</strong>
            {relation.observations.length ? (
              <ul>
                {relation.observations.map((observation, index) => (
                  <li key={`${relation.relationId}-${observation.kind}-${index}`}>
                    <span>{observation.status === "verified" ? "已核验" : observation.status}</span>
                    {observation.summary}
                    <small>{observation.asOfDate ?? "未标注日期"}{observation.sourceStatementId ? ` · 原子陈述 ${observation.sourceStatementId}` : " · 无来源陈述"}</small>
                  </li>
                ))}
              </ul>
            ) : <p>尚无可审计观察，不能把这条关系计入正式结论。</p>}
          </article>
        ))}
      </div>
    </section>
  );
}

function FundRow({ fund }: { fund: EventImpactFund }) {
  const sourceValue = typeof fund.source === "string" && fund.source.trim()
    ? fund.source.trim() : "来源待补充";
  const source = safeExternalHref(sourceValue);
  const coverageRatio = Number.isFinite(fund.coverageRatio) ? fund.coverageRatio : 0;
  const coverageState = fund.coverageStatus === "stale"
    ? "披露已过时，不计算敞口"
    : fund.coverageStatus === "partial"
      ? "覆盖不完整，不计算敞口"
      : fund.computable
        ? "覆盖完整，敞口可计算"
        : "覆盖不足，不计算敞口";
  return (
    <article className="impact-fund-row">
      <div>
        <strong>{fund.fundName || "基金名称待补"}</strong>
        <span>{fund.fundCode || "代码待补"} · 截至 {fund.reportPeriod || "日期待补"}</span>
      </div>
      <dl>
        <div><dt>覆盖</dt><dd>{Math.round(coverageRatio * 100)}% · {coverageState}</dd></div>
        <div><dt>敞口</dt><dd>{fund.computable ? fund.exposure ?? "未计算" : "覆盖不足，不计算"}</dd></div>
        <div><dt>可见时间</dt><dd>{fund.publishedAt || "时间待补"}</dd></div>
      </dl>
      {source ? <a href={source} target="_blank" rel="noopener noreferrer">查看披露来源</a> : <small>{sourceValue}</small>}
    </article>
  );
}

export function EventImpactTraceScreen() {
  const { caseId = "" } = useParams();
  const [workbench, setWorkbench] = useState<EventWorkbench | null>(null);
  const [trace, setTrace] = useState<EventImpactTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<EventImpactRelation | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const reviewTrigger = useRef<HTMLButtonElement | null>(null);
  const [selectedFactorId, setSelectedFactorId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!caseId) return;
    try {
      setError(null);
      const [nextWorkbench, nextTrace] = await Promise.all([
        researchClient.getEventWorkbench(caseId),
        researchClient.getEventImpactTrace!(caseId),
      ]);
      setWorkbench(nextWorkbench);
      setTrace(nextTrace);
      const currentScopeFactors = [...nextTrace.factors, ...nextTrace.alternatives];
      setSelectedFactorId((current) => (
        current && currentScopeFactors.some((factor) => factor.hypothesisId === current)
          ? current : nextTrace.factors[0]?.hypothesisId ?? null
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "请稍后重试");
    }
  }, [caseId]);

  useEffect(() => { void load(); }, [load]);

  const submitReview = async (outcome: ReviewOutcome, reason: string, reviewer: string) => {
    if (!reviewing || submitting) return;
    setSubmitting(true);
    setReviewError(null);
    try {
      await researchClient.reviewEventImpactRelation!({ relationId: reviewing.relationId, outcome, reason: reason.trim(), reviewer: reviewer.trim() });
      setStatus("已记录审核结果。关系仍需遵循其来源和当前 scope 的边界。");
      await load();
      setReviewing(null);
      window.setTimeout(() => reviewTrigger.current?.focus(), 0);
    } catch (reason) {
      setReviewError(reason instanceof Error ? reason.message : "请检查审核内容后重试");
    } finally {
      setSubmitting(false);
    }
  };

  if (error) return <main className="prototype-screen"><p className="form-error">影响传导加载失败：{error}</p><button className="prototype-button" type="button" onClick={() => void load()}>重试</button></main>;
  if (!workbench || !trace) return <main className="prototype-screen impact-trace-screen"><p>正在整理影响传导…</p></main>;
  const currentScopeFactors = [...trace.factors, ...trace.alternatives];
  const selectedFactor = currentScopeFactors.find((factor) => factor.hypothesisId === selectedFactorId)
    ?? currentScopeFactors[0]
    ?? null;

  return <main className="prototype-screen impact-trace-screen" data-layout="conclusion-first">
    <header className="event-page-header">
      <div><p className="section-kicker">事件研究 · 当前 scope {trace.scopeVersion}</p><h1>影响传导</h1><p>{workbench.event.eventTitle}，以当前范围的可审计关系为准。</p></div>
      <Link className="prototype-button" to={`/events/${caseId}`}>返回结论工作台</Link>
    </header>
    <section className="impact-layer impact-conclusion" aria-labelledby="impact-conclusion-title">
      <p className="section-kicker">第一层 · 当前判断</p>
      <h2 id="impact-conclusion-title">当前判断</h2>
      <p>{workbench.conclusion.text}</p>
      <div className="impact-conclusion__meta"><span>置信度：{workbench.conclusion.confidence === "high" ? "高" : workbench.conclusion.confidence === "medium" ? "中" : "低"}</span><span>当前缺口：{workbench.progress.currentGap ?? "未发现明确缺口"}</span>{selectedFactor ? <span>当前查看：{selectedFactor.statement}</span> : null}</div>
    </section>
    {status ? <p className="impact-review-status" role="status">{status}</p> : null}
    <section className="impact-layer impact-transmission" aria-labelledby="impact-transmission-title">
      <div className="impact-layer__heading"><div><p className="section-kicker">第二层</p><h2 id="impact-transmission-title">传导关系</h2></div><span>{trace.progress.relations} 条当前关系</span></div>
      <div className="impact-factor-selector" role="group" aria-label="选择影响因素">
        {currentScopeFactors.map((factor) => <button key={factor.hypothesisId} type="button" aria-pressed={factor.hypothesisId === selectedFactor?.hypothesisId} onClick={() => setSelectedFactorId(factor.hypothesisId)}>{factor.statement}</button>)}
      </div>
      {selectedFactor ? <FactorRelations factor={selectedFactor} onReview={(relation) => { reviewTrigger.current = document.activeElement as HTMLButtonElement; setReviewError(null); setReviewing(relation); }} /> : null}
      {!currentScopeFactors.length ? <p className="impact-empty">当前 scope 尚未形成影响假设，系统不会用旧范围替代。</p> : null}
    </section>
    <EvidenceLayer trace={{ ...trace, factors: selectedFactor ? [selectedFactor] : [] }} />
    <section className="impact-layer impact-funds" aria-labelledby="impact-funds-title">
      <div className="impact-layer__heading"><div><p className="section-kicker">第四层</p><h2 id="impact-funds-title">基金披露覆盖</h2></div><span>只纳入中国基金、可见的 A 股持仓</span></div>
      {selectedFactor?.funds.map((fund) => <FundRow key={`${fund.fundId}-${fund.reportPeriod}`} fund={fund} />)}
      {!selectedFactor?.funds.length ? <p className="impact-empty">当前没有满足时点和市场边界的基金披露，因此不展示敞口。</p> : null}
    </section>
    {reviewing ? <ReviewDialog relation={reviewing} submitting={submitting} error={reviewError} onSubmit={(outcome, reason, reviewer) => void submitReview(outcome, reason, reviewer)} onClose={() => { setReviewing(null); window.setTimeout(() => reviewTrigger.current?.focus(), 0); }} /> : null}
  </main>;
}
