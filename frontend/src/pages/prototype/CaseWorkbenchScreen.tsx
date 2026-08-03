import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  AssessmentReviewPayload,
  CaseSummaryItem,
  CaseWorkbenchView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const TABS = [
  "研究摘要",
  "关键图表",
  "核心观点",
  "风险与假设",
  "相关公司",
  "研究日志",
];

const RELATION_LABEL: Record<string, string> = {
  support: "支持",
  contradict: "反驳",
  gap: "缺口",
};

export function CaseWorkbenchScreen() {
  const params = useParams<{ caseId?: string }>();
  const caseId = params.caseId ?? "RC-AIC-2025-01";
  const [searchParams, setSearchParams] = useSearchParams();
  const initialThesisId = searchParams.get("thesis_id") ?? undefined;
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<CaseWorkbenchView | null>(null);
  const [tab, setTab] = useState(0);
  const [selectedCaseId, setSelectedCaseId] = useState<string>(caseId);
  const [selectedThesisId, setSelectedThesisId] = useState<string | undefined>(
    initialThesisId,
  );
  const [cases, setCases] = useState<CaseSummaryItem[]>([]);
  const [proposing, setProposing] = useState(false);
  const [proposeNotice, setProposeNotice] = useState<string | null>(null);
  const [proposeSucceeded, setProposeSucceeded] = useState(false);
  const [reviewer, setReviewer] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [modifiedConclusion, setModifiedConclusion] =
    useState<NonNullable<AssessmentReviewPayload["conclusion"]>>("supported");
  const [reviewing, setReviewing] = useState(false);
  const [reviewNotice, setReviewNotice] = useState<string | null>(null);
  const [caseQuery, setCaseQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    researchClient
      .listCaseSummaries()
      .then((list) => {
        if (!cancelled) setCases(list);
      })
      .catch(() => {
        /* 列表加载失败时仅隐藏侧栏内容，主视图不受影响 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadCaseView = useCallback(() => {
    return researchClient
      .getCaseWorkbenchView(selectedCaseId, {
        thesisId: selectedThesisId,
      })
      .then((v) => setView(v));
  }, [selectedCaseId, selectedThesisId]);

  useEffect(() => {
    let cancelled = false;
    loadCaseView()
      .then(() => {
        if (!cancelled) setState({ kind: "ready" });
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadCaseView]);

  const caseList = useMemo<CaseSummaryItem[]>(() => {
    // 通过 URL 直达的案例可能不在列表里（如 mock 新建后刷新），补在首位。
    if (cases.some((c) => c.id === selectedCaseId)) return cases;
    return [
      {
        id: selectedCaseId,
        title: view?.case.title ?? selectedCaseId,
        topic: view?.case.researchObject ?? "",
        updatedAt: "",
      },
      ...cases,
    ];
  }, [cases, view, selectedCaseId]);

  // 触发引擎 propose 步骤：AI 针对焦点命题提议证据关系，全部进入审核
  // 队列（无任何自动确认），随后引导用户去复核中心。
  const runPropose = () => {
    const thesisId =
      view?.nextValidation.thesisId || view?.thesisRows[0]?.id || "";
    if (!thesisId || proposing) return;
    setProposing(true);
    setProposeNotice(null);
    setProposeSucceeded(false);
    researchClient
      .proposeEvidence(thesisId)
      .then((r) => {
        setProposeNotice(
          r.linkCount > 0
            ? `AI 提议了 ${r.linkCount} 条证据关系，已进入审核队列。`
            : "AI 未提出新的证据关系（离线模式或无可提议证据）。",
        );
        setProposeSucceeded(r.linkCount > 0);
      })
      .catch((err: Error) => {
        setProposeNotice(`AI 提议未完成：${err.message || "未知错误"}`);
      })
      .finally(() => setProposing(false));
  };

  // 评估复核：对 provisional AI 评估写入人工决策（确认/修改/驳回），
  // 与复核中心的关系审核互补；写入后刷新案例视图。
  const submitAssessmentReview = (outcome: AssessmentReviewPayload["outcome"]) => {
    const assessmentId = view?.formalJudgment.assessmentId;
    if (!assessmentId || reviewing) return;
    setReviewing(true);
    setReviewNotice(null);
    researchClient
      .reviewAssessment(assessmentId, {
        outcome,
        conclusion: outcome === "modified" ? modifiedConclusion : undefined,
        reason: reviewReason || "（无补充理由）",
        reviewer: reviewer || "审核人",
      })
      .then(() => {
        setReviewNotice("评估复核已写入。");
        setReviewReason("");
        return loadCaseView();
      })
      .catch((err: Error) => {
        setReviewNotice(`评估复核未完成：${err.message || "未知错误"}`);
      })
      .finally(() => setReviewing(false));
  };

  const filteredCases = useMemo(() => {
    const q = caseQuery.trim();
    if (!q) return cases;
    return cases.filter(
      (c) => c.title.includes(q) || c.topic.includes(q),
    );
  }, [cases, caseQuery]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="case-workbench-loading">
        <p>正在加载研究案例…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="case-workbench-error">
        <div className="form-error">
          研究案例加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen case-workbench-screen" data-testid="case-workbench-screen">
      <div className="case-workbench-layout">
        <aside className="case-list" aria-label="行业案例列表">
          <div className="case-list__head">
            <span>行业案例</span>
            <Link to="/new-research" className="link-button" aria-label="新建研究">
              ＋
            </Link>
          </div>
          <input
            type="search"
            placeholder="搜索案例标题或关键词"
            className="case-list__search"
            value={caseQuery}
            onChange={(e) => setCaseQuery(e.target.value)}
          />
          <div className="case-list__filters">
            <button type="button" className="filter-pill is-active">
              全部 {caseList.length}
            </button>
          </div>
          <ul className="case-list__items">
            {filteredCases.map((c) => (
              <li
                key={c.id}
                className={`case-list__item${c.id === selectedCaseId ? " is-active" : ""}`}
                onClick={() => setSelectedCaseId(c.id)}
                role="button"
                tabIndex={0}
              >
                <strong>{c.title}</strong>
                <small>
                  {c.topic || "研究案例"}
                  {c.updatedAt ? ` · ${c.updatedAt.slice(0, 10)}` : ""}
                </small>
              </li>
            ))}
          </ul>
        </aside>

        <main className="case-main">
          <nav className="breadcrumb" aria-label="面包屑">
            <Link to="/workspace">行业研究</Link>
            <span>/</span>
            <span>{view.case.researchObject}</span>
            <span>/</span>
            <span>行业案例</span>
            <span>/</span>
            <strong>{view.case.title}</strong>
          </nav>

          <header className="case-main__head">
            <div>
              <div className="eyebrow">{view.case.researchObject}</div>
              <h1>{view.case.title}</h1>
              <div className="case-meta-row">
                <small>研究区间：{view.case.researchPeriod}</small>
                <small>证据截止：{view.case.cutoff.slice(0, 10)}</small>
                <span className="state-badge ai">{view.case.aiState}</span>
                <span className="state-badge warning">{view.case.humanReviewState}</span>
              </div>
            </div>
            <div className="case-main__actions">
              <button
                type="button"
                className="prototype-button primary"
                disabled={proposing}
                onClick={runPropose}
                data-testid="ai-propose-button"
              >
                {proposing ? "AI 提议中…" : "✦ AI 提议证据"}
              </button>
              <Link
                className="prototype-button"
                to={`/relationships/${selectedCaseId}`}
              >
                证据图谱 →
              </Link>
              <Link
                className="prototype-button"
                to={`/conclusion/${selectedCaseId}`}
                data-testid="link-conclusion"
              >
                结论与关键因素 →
              </Link>
              <Link className="prototype-button" to="/review">
                复核中心 →
              </Link>
              <Link className="prototype-button" to="/versions">
                版本对比 →
              </Link>
            </div>
            {proposeNotice ? (
              <p style={{ fontSize: 12, marginTop: 6 }} role="status">
                {proposeNotice}
                {proposeSucceeded ? (
                  <>
                    {" "}
                    <Link to="/review">去复核 →</Link>
                  </>
                ) : null}
              </p>
            ) : null}
          </header>

          <nav className="prototype-stepper" aria-label="案例档案选项卡">
            {view.tabs.map((t, i) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(i)}
                data-step-state={i === tab ? "current" : "upcoming"}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span>{TABS[i] ?? t}</span>
                <strong>{t}</strong>
              </button>
            ))}
          </nav>

          <section className="prototype-paper">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">当前判断</p>
                <h2>正式结论</h2>
              </div>
              <span
                className={`state-badge ${view.formalJudgment.reviewState === "reviewed" ? "reviewed" : "warning"}`}
              >
                {view.formalJudgment.reviewState === "reviewed"
                  ? "已人工复核"
                  : view.formalJudgment.reviewState === "failed"
                    ? "评估失败"
                    : "未人工复核"}
              </span>
            </div>
            <p style={{ fontSize: 14, fontWeight: 600 }}>{view.formalJudgment.text}</p>
            <ul className="case-bullets">
              <li>{view.formalJudgment.rationale}</li>
              <li>反驳线索：{view.contradiction.label}</li>
              <li>
                缺口：{view.gap.label}
                {view.gap.explanation ? `（${view.gap.explanation}）` : ""}
              </li>
              <li>下一步验证：{view.nextValidation.event}</li>
            </ul>
            <details>
              <summary>展开链路</summary>
              <div className="case-chain-row">
                {view.factorRows.flatMap((f, i) =>
                  i === 0
                    ? [<span key={f.factorId}>{f.label}</span>]
                    : [
                        <span key={`arrow-${f.factorId}`}>→</span>,
                        <span key={f.factorId}>{f.label}</span>,
                      ],
                )}
              </div>
            </details>
            {view.formalJudgment.reviewState !== "reviewed" &&
            view.formalJudgment.assessmentId ? (
              <div
                className="assessment-review-bar"
                style={{
                  marginTop: 12,
                  paddingTop: 10,
                  borderTop: "1px dashed var(--rule)",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 8,
                  alignItems: "center",
                  fontSize: 12,
                }}
              >
                <strong>评估复核</strong>
                <input
                  type="text"
                  placeholder="署名"
                  aria-label="复核署名"
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                  style={{ width: 90 }}
                />
                <input
                  type="text"
                  placeholder="复核理由（可选）"
                  aria-label="复核理由"
                  value={reviewReason}
                  onChange={(e) => setReviewReason(e.target.value)}
                  style={{ flex: 1, minWidth: 160 }}
                />
                <select
                  aria-label="修改后的结论"
                  value={modifiedConclusion}
                  onChange={(e) =>
                    setModifiedConclusion(
                      e.target.value as NonNullable<
                        AssessmentReviewPayload["conclusion"]
                      >,
                    )
                  }
                >
                  <option value="supported">改为：支持（成立）</option>
                  <option value="contradicted">改为：反驳（不成立）</option>
                  <option value="insufficient_evidence">改为：证据不足</option>
                </select>
                <button
                  type="button"
                  className="prototype-button primary"
                  disabled={reviewing}
                  onClick={() => submitAssessmentReview("confirmed")}
                  data-testid="assessment-review-confirm"
                >
                  确认
                </button>
                <button
                  type="button"
                  className="prototype-button"
                  disabled={reviewing}
                  onClick={() => submitAssessmentReview("modified")}
                >
                  按修改确认
                </button>
                <button
                  type="button"
                  className="prototype-button danger"
                  disabled={reviewing}
                  onClick={() => submitAssessmentReview("rejected")}
                >
                  驳回
                </button>
              </div>
            ) : null}
            {reviewNotice ? (
              <p style={{ fontSize: 12, marginTop: 6 }} role="status">
                {reviewNotice}
              </p>
            ) : null}
          </section>

          <section>
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">
                  支持证据 · {view.sources.filter((s) => s.relation === "support").length}
                </p>
                <h2>支撑结论的来源</h2>
              </div>
            </div>
            <ul className="case-evidence-list">
              {view.sources
                .filter((s) => s.relation === "support")
                .map((s) => (
                  <li key={s.id}>
                    <span
                      className={`state-badge ${s.frozenEligibility === "reviewed" ? "reviewed" : "warning"}`}
                    >
                      {s.reviewLabel}
                    </span>
                    <div>
                      <strong>{s.statement}</strong>
                      <small>
                        支持 · 截止可用：{s.publishedDate}
                      </small>
                    </div>
                  </li>
                ))}
            </ul>
          </section>

          <section>
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">
                  反证 · {view.sources.filter((s) => s.relation === "contradict").length}
                </p>
                <h2>可能反驳当前判断</h2>
              </div>
            </div>
            <ul className="case-evidence-list case-evidence-list--counter">
              {view.sources
                .filter((s) => s.relation === "contradict")
                .map((s) => (
                  <li key={s.id}>
                    <span className="state-badge contradict">反驳</span>
                    <div>
                      <strong>{s.statement}</strong>
                      <small>
                        反驳 · 截止可用：{s.publishedDate}
                      </small>
                    </div>
                  </li>
                ))}
            </ul>
          </section>
        </main>

        <aside className="case-pin" aria-label="原文定位面板">
          <header>
            <strong>原文摘要（已定位）</strong>
            <span
              className={`state-badge ${view.rebuttal.reviewState === "reviewed" ? "reviewed" : "warning"}`}
            >
              {view.rebuttal.reviewLabel}
            </span>
          </header>
          <div className="case-pin__source">
            <span className="state-badge reviewed">反驳证据 · 定位</span>
            <strong>{view.rebuttal.documentTitle}</strong>
          </div>
          <p className="case-pin__excerpt">{view.rebuttal.statement}</p>
          <p className="case-pin__source-line">原文片段：{view.rebuttal.sourceSpan}</p>

          <section className="case-pin__position">
            <h3>定位信息</h3>
            <dl>
              <div><dt>Span</dt><dd>{view.rebuttal.documentId}</dd></div>
              <div><dt>截止可用</dt><dd>{view.rebuttal.publishedDate}</dd></div>
            </dl>
          </section>

          <section className="case-pin__evidence">
            <h3>证据信息</h3>
            <dl>
              <div><dt>证据 ID</dt><dd>{view.rebuttal.id.slice(0, 8)}</dd></div>
              <div><dt>证据角色</dt><dd>反驳</dd></div>
              <div><dt>快照归属</dt><dd>{view.rebuttal.snapshotMembership}</dd></div>
              <div><dt>冻结资格</dt><dd>{view.rebuttal.frozenEligibility}</dd></div>
            </dl>
          </section>

          <section className="case-pin__review">
            <h3>复核状态</h3>
            <span
              className={`state-badge ${view.rebuttal.reviewState === "reviewed" ? "reviewed" : "warning"}`}
            >
              {view.rebuttal.reviewLabel}
            </span>
            <p>
              {view.rebuttal.reviewState === "reviewed"
                ? "此证据已经过人工复核。"
                : "此证据尚未经过人工复核。"}
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}