import { useCallback, useEffect, useMemo, useState } from "react";
import { researchClient } from "../../data/researchClient";
import type {
  ReviewOutcome,
} from "../../domain/types";
import type {
  CaseWorkbenchRebuttal,
  NewResearchView,
  ReviewQueueViewItem,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

type PrototypeRole = "support" | "contradict" | "gap";

interface PendingItem {
  id: string;
  title: string;
  source: string;
  sourceSpan: string;
  publishedAt: string;
  reviewLabel: string;
  reviewState: string;
  snapshotMembership: string;
  priority: string;
  rationale: string;
  documentId: string;
  statementId: string;
  role: PrototypeRole;
}

function mapQueueItem(item: ReviewQueueViewItem): PendingItem {
  const role: PrototypeRole =
    item.aiRole === "supports"
      ? "support"
      : item.aiRole === "contradicts"
        ? "contradict"
        : "gap";
  return {
    id: item.linkId,
    title: item.statementText,
    source: item.documentSourceUrl,
    sourceSpan: item.verbatimText,
    publishedAt: item.documentPublishedAt ?? "",
    reviewLabel: "待人工审核",
    reviewState: "pending_review",
    snapshotMembership: "",
    priority: "",
    rationale: item.aiReason,
    documentId: item.documentVersionId,
    statementId: item.statementId,
    role,
  };
}

export function ReviewWorkbenchScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<NewResearchView | null>(null);
  const [queueItems, setQueueItems] = useState<ReviewQueueViewItem[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [decision, setDecision] = useState<ReviewOutcome>("confirmed");
  const [relationChoice, setRelationChoice] = useState<"支持" | "反驳" | "背景" | "证据缺口">(
    "支持",
  );
  const [factor, setFactor] = useState<string>("");
  const [boundary, setBoundary] = useState<string>(
    "仅适用于当前前部截止日与该分部口径",
  );
  const [reviewNotes, setReviewNotes] = useState("");
  const [reason, setReason] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submittedCount, setSubmittedCount] = useState(0);

  const loadQueue = useCallback(() => {
    return researchClient.getReviewQueueView().then((q) => {
      setQueueItems(q.items);
      setSelectedId((prev) =>
        q.items.some((i) => i.linkId === prev) ? prev : (q.items[0]?.linkId ?? ""),
      );
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([researchClient.getNewResearchView(), loadQueue()])
      .then(([v]) => {
        if (!cancelled) {
          setView(v);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadQueue]);

  const RELATION_MAP = {
    支持: "supports",
    反驳: "contradicts",
    背景: "contextualizes",
    证据缺口: "evidence_gap",
  } as const;

  const submitReview = (outcome: "confirmed" | "rejected" | "needs_more_evidence") => {
    const current = queueItems.find((i) => i.linkId === selectedId);
    if (!current || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    researchClient
      .submitLinkReview(current.linkId, {
        outcome,
        relation: RELATION_MAP[relationChoice],
        factor_role: factor || "未指定因素",
        scope_boundary: boundary || "未填写边界",
        reason: reviewNotes || reason || "（无补充理由）",
        reviewer: reviewer || "审核人",
      })
      .then(() => {
        setSubmittedCount((c) => c + 1);
        setFactor("");
        setReviewNotes("");
        setReason("");
        return loadQueue();
      })
      .catch((err: Error) => {
        setSubmitError(err.message || "审核写入失败");
      })
      .finally(() => setSubmitting(false));
  };

  const pending: PendingItem[] = useMemo(
    () => queueItems.map(mapQueueItem),
    [queueItems],
  );

  const selected = pending.find((p) => p.id === selectedId) ?? pending[0];
  const rebuttalStub: CaseWorkbenchRebuttal = {
    id: "EL-004",
    statement:
      "AI 基础设施需求高于可供容量，资本投入需待产能上线后支持收入。",
    documentId: "DOC-MSFT-FY25Q3-CALL",
    documentTitle: "Microsoft FY2025 Q3 业绩说明会记录",
    sourceVersion: "issuer-call-2025-04-30-v1",
    publishedDate: "2025-04-30",
    sourceSpan: "prepared remarks, pp. 4-5, capacity constraints and revenue timing",
    reviewLabel: "已人工复核 · 林岚",
    reviewState: "reviewed",
    relation: "contradict",
    snapshotMembership: "RS-2025-06-30-v3",
    frozenEligibility: "reviewed",
  };

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="review-loading">
        <p>正在加载审核工作区…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="review-error">
        <div className="form-error">
          审核工作区加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }
  if (!selected) {
    return (
      <div className="prototype-screen" data-testid="review-screen">
        <header>
          <div className="eyebrow">审核中心 · Review Workbench</div>
          <h1>关系审核</h1>
        </header>
        <p className="lede" data-testid="review-empty">
          审核队列已清空{submittedCount > 0 ? `，本次会话已写入 ${submittedCount} 项人工决策` : ""}。
        </p>
      </div>
    );
  }

  return (
    <div className="prototype-screen" data-testid="review-screen">
      <header>
        <div className="eyebrow">审核中心 · Review Workbench</div>
        <h1>关系审核</h1>
        <p className="lede">
          审核 <code>{pending.length}</code> 项 AI 提议关系
          {submittedCount > 0 ? (
            <>
              {" "}· 本次会话已写入 <code>{submittedCount}</code> 项人工决策
            </>
          ) : null}
        </p>
      </header>

      <div className="prototype-review-workbench">
        <aside className="review-queue-panel">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">审核队列</p>
              <h2>{pending.length} 项等待</h2>
            </div>
          </div>
          <ul className="prototype-review-queue">
            {pending.map((p) => (
              <li key={p.id}>
                <a
                  href={`#${p.id}`}
                  className={p.id === selectedId ? "is-selected" : ""}
                  onClick={(e) => {
                    e.preventDefault();
                    setSelectedId(p.id);
                  }}
                >
                  <div className="review-item-top">
                    <span>{p.id.slice(0, 8)}</span>
                    {p.priority ? (
                      <span
                        className={`review-priority ${
                          p.priority === "high"
                            ? "high"
                            : p.priority === "medium"
                              ? "medium"
                              : "low"
                        }`}
                      >
                        {p.priority}
                      </span>
                    ) : (
                      <span className="ai-boundary">AI 提议</span>
                    )}
                  </div>
                  <strong>{p.title}</strong>
                  <dl>
                    <div>
                      <dt>来源</dt>
                      <dd>{p.source}</dd>
                    </div>
                    <div>
                      <dt>出处</dt>
                      <dd>{p.sourceSpan}</dd>
                    </div>
                  </dl>
                </a>
              </li>
            ))}
          </ul>
        </aside>

        <section className="review-comparison">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">对比阅读</p>
              <h2>已冻结来源 · AI 提议 · 不变记录</h2>
            </div>
          </div>

          <div className="prototype-frozen-source">
            <strong>{selected.id}</strong>
            <span className="state-badge reviewed" style={{ float: "right" }}>
              已冻结来源 · {selected.reviewLabel}
            </span>
            <p>{selected.rationale}</p>
            <blockquote>"{selected.title}"</blockquote>
            <dl className="source-metadata">
              <div>
                <dt>来源版本</dt>
                <dd>{selected.source}</dd>
              </div>
              <div>
                <dt>出处</dt>
                <dd>{selected.sourceSpan}</dd>
              </div>
              <div>
                <dt>发表日期</dt>
                <dd>{selected.publishedAt}</dd>
              </div>
              <div>
                <dt>所属快照</dt>
                <dd>{selected.snapshotMembership || "—"}</dd>
              </div>
              <div>
                <dt>原文 ID</dt>
                <dd>{selected.documentId}</dd>
              </div>
              <div>
                <dt>陈述 ID</dt>
                <dd>{selected.statementId}</dd>
              </div>
            </dl>
          </div>

          <div className="prototype-ai-proposal">
            <strong style={{ display: "flex", alignItems: "center" }}>
              AI 提议关系
              <span className="ai-boundary">未经人工复核</span>
            </strong>
            <p>
              关系：{selected.role === "support" ? "支持" : selected.role === "contradict" ? "反驳" : "缺口"}
              ，目标陈述 {selected.statementId}。
            </p>
            <p>提议理由（由模型给出，未经人工复核）：</p>
            <blockquote>
              "{selected.rationale}"
            </blockquote>
            <p style={{ fontSize: 11 }}>
              该提议仅作待审材料；纳入正式判断前必须经过人工核对。
            </p>
          </div>

          <div className="prototype-immutable-record">
            <strong>当前冻结的不变记录</strong>
            <p style={{ fontSize: 12 }}>
              已确认的反面证据（示例数据，待案例工作台接口接入）：
            </p>
            <blockquote>"{rebuttalStub.statement}"</blockquote>
            <dl className="source-metadata">
              <div>
                <dt>文档</dt>
                <dd>{rebuttalStub.documentTitle}</dd>
              </div>
              <div>
                <dt>版本</dt>
                <dd>{rebuttalStub.sourceVersion}</dd>
              </div>
              <div>
                <dt>出处</dt>
                <dd>{rebuttalStub.sourceSpan}</dd>
              </div>
              <div>
                <dt>关系</dt>
                <dd>反驳</dd>
              </div>
              <div>
                <dt>审核</dt>
                <dd>{rebuttalStub.reviewLabel}</dd>
              </div>
              <div>
                <dt>快照</dt>
                <dd>{rebuttalStub.snapshotMembership}</dd>
              </div>
            </dl>
          </div>
        </section>

        <aside className="human-decision">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">审核人决策</p>
              <h2>只有人可写入</h2>
            </div>
          </div>
          <p className="muted" style={{ fontSize: 11 }}>
            以下 4 项必须由审核人明确填写，写入系统后不可逆。
          </p>
          <form
            className="prototype-review-form"
            onSubmit={(e) => {
              e.preventDefault();
              submitReview("confirmed");
            }}
          >
            <fieldset>
              <legend>
                <span>0. 审核人署名</span>
                <small>必填</small>
              </legend>
              <input
                type="text"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                placeholder="你的名字（写入审计记录）"
                aria-label="审核人署名"
              />
            </fieldset>

            <fieldset>
              <legend>
                <span>1. 关系选择</span>
                <small>必选</small>
              </legend>
              <div className="choice-row">
                {(["支持", "反驳", "背景", "证据缺口"] as const).map((r) => (
                  <label
                    key={r}
                    className={`choice${relationChoice === r ? " is-selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="relation"
                      value={r}
                      checked={relationChoice === r}
                      onChange={() => setRelationChoice(r)}
                    />
                    {r}
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend>
                <span>2. 因素选择</span>
                <small>必选</small>
              </legend>
              <select
                value={factor}
                onChange={(e) => setFactor(e.target.value)}
                aria-label="审核因素"
              >
                <option value="">—— 请选择因素 ——</option>
                <option>分部盈利预期上修</option>
                <option>供应链临界点已越</option>
                <option>同比交付数量大幅低于预期</option>
                <option>需求边缘转化为完整成本披露</option>
              </select>
              <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                写明对应市场与你一致或不一致；不写则留作补证证据。
              </p>
            </fieldset>

            <fieldset>
              <legend>
                <span>3. 适用边界</span>
                <small>必填</small>
              </legend>
              <textarea
                rows={2}
                placeholder="例：仅适用于当前前部截止日与该分部口径"
                value={boundary}
                onChange={(e) => setBoundary(e.target.value)}
              />
            </fieldset>

            <fieldset>
              <legend>
                <span>4. 审核理由</span>
                <small>必填</small>
              </legend>
              <textarea
                rows={3}
                placeholder="写下你的核对结果、修正意见或拒绝原因…"
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
              />
              <div className="review-counter">
                <span>{reviewNotes.length} / 280</span>
                <span>路径 0 / 4 已完成</span>
              </div>
            </fieldset>

            <div className="prototype-review-actions">
              <button
                type="submit"
                className="prototype-button primary"
                disabled={submitting}
              >
                {submitting ? "写入中…" : "确认并写入审核记忆"}
              </button>
              <button
                type="button"
                className="prototype-button"
                disabled={submitting}
                onClick={() => submitReview("rejected")}
              >
                驳回
              </button>
              <button
                type="button"
                className="prototype-button"
                disabled={submitting}
                onClick={() => submitReview("needs_more_evidence")}
              >
                要求补充证据
              </button>
              {submitError ? (
                <span className="form-error" role="alert">
                  {submitError}
                </span>
              ) : (
                <span className="prototype-page-state">
                  完成该项人工判别后，确认动作才会解锁。
                </span>
              )}
            </div>
          </form>
        </aside>
      </div>
    </div>
  );
}