import { useEffect, useState } from "react";
import { researchClient } from "../data/researchClient";
import { useResearchQuery } from "../data/useResearchQuery";
import type { Conclusion, ReviewQueueItem, ReviewOutcome } from "../domain/types";
import { StatusMark } from "../components/StatusMark";
import { PageStateBanners } from "../components/PageStateBanners";
import { Button } from "../components/primitives/Button";

const CONCLUSION_OPTIONS: { value: Conclusion; label: string }[] = [
  { value: "supported", label: "支持" },
  { value: "contradicted", label: "反证" },
  { value: "insufficient_evidence", label: "证据不足" },
];

export function ReviewWorkbenchPage() {
  const queueState = useResearchQuery<ReviewQueueItem[]>(
    () => researchClient.getReviewQueue(),
    []
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [conclusion, setConclusion] = useState<Conclusion>("supported");
  const [reason, setReason] = useState("");
  const [history, setHistory] = useState<
    { id: string; text: string; outcome: ReviewOutcome }[]
  >([]);
  const [submitting, setSubmitting] = useState(false);

  const queue = queueState.data;
  const selected = queue?.find((i) => i.id === selectedId) ?? null;

  // 自动选中第一条或下一条（处理完成后剩余的）
  useEffect(() => {
    if (selectedId !== null) return;
    if (queue && queue[0]) setSelectedId(queue[0].id);
  }, [queue, selectedId]);

  async function act(outcome: ReviewOutcome) {
    if (!selected) return;
    setSubmitting(true);
    try {
      await researchClient.submitReviewDecision(selected.id, {
        outcome,
        conclusion: outcome === "modified" ? conclusion : null,
        reason: reason || (outcome === "confirmed" ? "认可 AI 提议" : outcome === "rejected" ? "驳回" : "修改后保留"),
      });
      setHistory((h) => [
        {
          id: `${selected.id}-${Date.now()}`,
          text: `${outcome} · ${reason || "（无理由）"}`,
          outcome,
        },
        ...h,
      ]);
      // 跳到下一条
      const idx = queue?.findIndex((i) => i.id === selected.id) ?? -1;
      const remaining = (queue ?? []).filter((i) => i.id !== selected.id);
      const next = remaining[idx] ?? remaining[0] ?? null;
      setSelectedId(next?.id ?? null);
      setReason("");
      setConclusion("supported");
    } finally {
      setSubmitting(false);
    }
  }

  async function skip() {
    if (!selected) return;
    setHistory((h) => [
      { id: `${selected.id}-skip`, text: "skip", outcome: "rejected" },
      ...h,
    ]);
    const remaining = (queue ?? []).filter((i) => i.id !== selected.id);
    setSelectedId(remaining[0]?.id ?? null);
  }

  return (
    <section className="page page--review" data-testid="review-workbench">
      <header className="page__header">
        <h1>审核队列</h1>
        <p className="muted">
          所有正式关系创建都要求人工动作。AI 提议仅供参考，连续处理，支持跳过。
        </p>
      </header>
      <PageStateBanners
        error={queueState.error}
        isHistorical={false}
        writeDisabled={
          queueState.error?.kind === "backend_unavailable" ||
          queueState.error?.kind === "permission_denied"
        }
      />

      <div className="review-layout">
        <aside className="review-queue" aria-label="待审核项">
          <h2>待审核项</h2>
          {queueState.error?.kind === "permission_denied" && (
            <p className="muted">
              权限不足，写操作已禁用；你可以浏览已有提议，但不能确认、修改或驳回。
            </p>
          )}
          {queueState.loading && !queue && (
            <div className="skeleton__line" />
          )}
          {queue && queue.length === 0 && (
            <p className="muted">当前没有待审核项。</p>
          )}
          <ul>
            {queue?.map((it) => (
              <li
                key={it.id}
                className={`review-queue__item${
                  selectedId === it.id ? " is-active" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={() => setSelectedId(it.id)}
                  aria-current={selectedId === it.id ? "true" : undefined}
                  data-testid={`review-item-${it.id}`}
                >
                  <span className="review-queue__kind" data-kind={it.kind}>
                    {labelFor(it.kind)}
                  </span>
                  <span className="review-queue__title">{it.preview}</span>
                  <span className="review-queue__meta muted">
                    {it.case_title} · {it.proposed_at}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section className="review-source" aria-label="冻结原文">
          <header>
            <h2>冻结原文</h2>
            {selected && <StatusMark status="ai_pending_review" />}
          </header>
          {!selected && (
            <p className="muted">选择左侧待审核项以查看 AI 提议与冻结原文。</p>
          )}
          {selected && (
            <article>
              <h3>{selected.preview}</h3>
              <p className="muted">
                期间 {selected.available_at} · 范围 {JSON.stringify(selected.scope)}
              </p>
              <blockquote>
                （冻结原文预览，遵循三层浏览：原文 + SourceSpan 标注 + 引用关系）
              </blockquote>
              <p className="muted">
                此项由 AI 于 {selected.proposed_at} 提议；正式关系创建需要人工动作。
              </p>
            </article>
          )}
        </section>

        <section className="review-decision" aria-label="人工决定">
          <h2>人工决定</h2>
          {!selected && <p className="muted">无待审核项。</p>}
          {selected && (
            <>
              <p className="muted">
                支持、反驳、背景、范围与理由均在当前页面完成，不弹出模态框。
              </p>
              <div className="review-decision__actions">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => act("confirmed")}
                  disabled={submitting}
                  data-testid="review-confirm"
                >
                  确认
                </Button>
                <Button
                  variant="chip"
                  size="sm"
                  onClick={() => act("modified")}
                  disabled={submitting}
                  data-testid="review-modify"
                >
                  修改
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => act("rejected")}
                  disabled={submitting}
                  data-testid="review-reject"
                >
                  驳回
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={skip}
                  disabled={submitting}
                  data-testid="review-skip"
                >
                  跳过
                </Button>
              </div>
              <label className="review-decision__reason">
                <span>结论（修改时使用）</span>
                <select
                  value={conclusion}
                  onChange={(e) => setConclusion(e.target.value as Conclusion)}
                >
                  {CONCLUSION_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="review-decision__reason">
                <span>理由</span>
                <textarea
                  rows={4}
                  placeholder="补充理由或修改意见（不会替换 AI 原提议）"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
            </>
          )}
          <h3>本次会话记录</h3>
          <ol className="review-decision__history">
            {history.map((h) => (
              <li key={h.id} data-outcome={h.outcome}>
                {h.text}
              </li>
            ))}
            {history.length === 0 && (
              <li className="muted">尚未提交任何决定。</li>
            )}
          </ol>
        </section>
      </div>
    </section>
  );
}

function labelFor(kind: ReviewQueueItem["kind"]): string {
  switch (kind) {
    case "evidence_link":
      return "证据链接";
    case "causal_edge":
      return "因果边";
    case "statement":
      return "陈述规范化";
    case "entity_alignment":
      return "实体对齐";
  }
}