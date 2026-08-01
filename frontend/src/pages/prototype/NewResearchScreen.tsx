import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import {
  DRAFT_FIELDS,
  FIELD_LIMITS,
  type DraftField,
  type NewResearchView,
  type ThesisDraft,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

interface ThesisDraftState {
  thesis: ThesisDraft;
  touched: boolean;
}

const STEP_LABELS = [
  { id: "1", label: "研究问题", state: "completed" as const },
  { id: "2", label: "初始命题", state: "current" as const },
  { id: "3", label: "已有资产", state: "upcoming" as const },
  { id: "4", label: "研究计划", state: "upcoming" as const },
];

function emptyThesis(id: string): ThesisDraft {
  return {
    id,
    origin: "human",
    lastEditedBy: "human",
    title: "",
    statement: "",
    observationStart: "",
    observationEnd: "",
    supportCondition: "",
    falsifier: "",
    nextValidationEvent: "",
  };
}

export function NewResearchScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<NewResearchView | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ThesisDraftState>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [createdCaseId, setCreatedCaseId] = useState<string | null>(null);
  const [caseForm, setCaseForm] = useState({
    title: "",
    industryTopic: "",
    researchObject: "",
    phenomenon: "",
    periodStart: "",
    periodEnd: "",
  });

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getNewResearchView()
      .then((v) => {
        if (cancelled) return;
        setView(v);
        const next: Record<string, ThesisDraftState> = {};
        for (const t of v.theses) {
          next[t.id] = { thesis: t, touched: false };
        }
        setDrafts(next);
        setState({ kind: "ready" });
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const confirmedCount = useMemo(() => {
    return Object.values(drafts).filter(
      (d) => d.touched && d.thesis.title.trim().length > 0,
    ).length;
  }, [drafts]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="new-research-loading">
        <p>正在加载新建研究…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="new-research-error">
        <div className="form-error">
          新建研究数据加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  function updateField(id: string, field: DraftField, value: string) {
    setDrafts((prev) => {
      const cur = prev[id];
      if (!cur) return prev;
      return {
        ...prev,
        [id]: {
          thesis: { ...cur.thesis, [field]: value, lastEditedBy: "human" },
          touched: true,
        },
      };
    });
  }

  function addBlankThesis() {
    const id = `TH-DRAFT-${Object.keys(drafts).length + 1}`;
    setDrafts((prev) => ({
      ...prev,
      [id]: { thesis: emptyThesis(id), touched: true },
    }));
  }

  function removeThesis(id: string) {
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  function validateAndConfirm(): boolean {
    setSubmitError(null);
    const issues: string[] = [];
    if (!caseForm.title.trim()) issues.push("案例标题不能为空");
    if (!caseForm.industryTopic.trim()) issues.push("行业主题不能为空");
    for (const draft of Object.values(drafts)) {
      const { thesis, touched } = draft;
      if (!touched) continue;
      if (!thesis.title.trim()) issues.push(`${thesis.id}：标题不能为空`);
      if (!thesis.statement.trim()) issues.push(`${thesis.id}：命题表达不能为空`);
      for (const field of DRAFT_FIELDS) {
        const value = thesis[field];
        if (typeof value === "string") {
          if (value.length > FIELD_LIMITS[field]) {
            issues.push(`${thesis.id}：${field} 超过字符上限`);
          }
        }
      }
    }
    if (issues.length) {
      setSubmitError(issues.join("；"));
      return false;
    }
    return true;
  }

  async function confirmAndCreate() {
    if (!validateAndConfirm()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const theses = Object.values(drafts)
        .filter((d) => d.touched && d.thesis.statement.trim())
        .map((d) => ({
          statement: d.thesis.statement,
          title: d.thesis.title || undefined,
          observationStart: d.thesis.observationStart || undefined,
          observationEnd: d.thesis.observationEnd || undefined,
          supportCondition: d.thesis.supportCondition || undefined,
          falsificationCondition: d.thesis.falsifier || undefined,
          nextVerificationEvent: d.thesis.nextValidationEvent || undefined,
          creatorType: "human" as const,
        }));
      const result = await researchClient.createCase({
        title: caseForm.title,
        industryTopic: caseForm.industryTopic,
        createdBy: "prototype-user",
        researchObject: caseForm.researchObject || undefined,
        phenomenon: caseForm.phenomenon || undefined,
        periodStart: caseForm.periodStart || undefined,
        periodEnd: caseForm.periodEnd || undefined,
        theses,
      });
      setCreatedCaseId(result.caseId);
    } catch (err) {
      setSubmitError(`创建失败：${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  const thesisEntries = Object.values(drafts);

  return (
    <div className="prototype-screen" data-testid="new-research-screen">
      <header>
        <div className="eyebrow">新建研究 · New Research</div>
        <h1>{view.caseTitle}</h1>
        <p className="lede">{view.caseQuestion}</p>
      </header>

      <ol className="prototype-stepper" aria-label="研究流程">
        {STEP_LABELS.map((step) => (
          <li key={step.id} data-step-state={step.state}>
            <span>阶段 {step.id}</span>
            <strong>{step.label}</strong>
          </li>
        ))}
      </ol>

      <section className="prototype-paper">
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">研究对象</p>
            <h2>研究范围与现象</h2>
          </div>
          <span className="state-badge ai">草稿</span>
        </div>
        <div className="thesis-fields">
          <label className="title-field">
            案例标题
            <input
              type="text"
              value={caseForm.title}
              onChange={(e) =>
                setCaseForm((p) => ({ ...p, title: e.target.value }))
              }
              aria-label="案例标题"
              placeholder="例如：AI 算力链"
            />
          </label>
          <label>
            行业主题
            <input
              type="text"
              value={caseForm.industryTopic}
              onChange={(e) =>
                setCaseForm((p) => ({ ...p, industryTopic: e.target.value }))
              }
              aria-label="行业主题"
              placeholder="例如：ai_compute"
            />
          </label>
          <label className="statement-field">
            研究对象
            <textarea
              rows={2}
              value={caseForm.researchObject}
              onChange={(e) =>
                setCaseForm((p) => ({ ...p, researchObject: e.target.value }))
              }
              aria-label="研究对象"
            />
          </label>
          <label className="statement-field">
            观察现象
            <textarea
              rows={2}
              value={caseForm.phenomenon}
              onChange={(e) =>
                setCaseForm((p) => ({ ...p, phenomenon: e.target.value }))
              }
              aria-label="观察现象"
            />
          </label>
          <label>
            研究期间开始
            <input
              type="date"
              value={caseForm.periodStart}
              onChange={(e) =>
                setCaseForm((p) => ({ ...p, periodStart: e.target.value }))
              }
            />
          </label>
          <label>
            研究期间结束
            <input
              type="date"
              value={caseForm.periodEnd}
              onChange={(e) =>
                setCaseForm((p) => ({ ...p, periodEnd: e.target.value }))
              }
            />
          </label>
        </div>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">研究命题</p>
            <h2>录入或编辑命题（{thesisEntries.length} 条）</h2>
          </div>
          <span className="state-badge ai">{view.stageStatus}</span>
        </div>

        {submitError && (
          <div className="form-error" role="alert">
            {submitError}
          </div>
        )}

        <div className="prototype-thesis-cards-row">
        {thesisEntries.map(({ thesis }) => (
          <fieldset key={thesis.id} className="prototype-thesis-editor prototype-thesis-card">
            <legend>
              <span className="thesis-card-id">命题 {thesis.id}</span>
              <span className="state-badge ai">
                {thesis.origin === "ai" ? "AI 草案" : "人工新增"}
              </span>
              {thesis.lastEditedBy === "human" && (
                <span className="state-badge reviewed">已人工编辑</span>
              )}
            </legend>
            <div className="thesis-fields">
              <label className="title-field">
                命题标题
                <input
                  type="text"
                  value={thesis.title}
                  onChange={(e) => updateField(thesis.id, "title", e.target.value)}
                  aria-label="命题标题"
                  maxLength={FIELD_LIMITS.title}
                />
              </label>
              <label className="statement-field">
                命题表达
                <textarea
                  rows={2}
                  value={thesis.statement}
                  onChange={(e) =>
                    updateField(thesis.id, "statement", e.target.value)
                  }
                  aria-label="命题陈述"
                  maxLength={FIELD_LIMITS.statement}
                />
              </label>
              <label>
                观察开始
                <input
                  type="date"
                  value={thesis.observationStart}
                  onChange={(e) =>
                    updateField(thesis.id, "observationStart", e.target.value)
                  }
                />
              </label>
              <label>
                观察结束
                <input
                  type="date"
                  value={thesis.observationEnd}
                  onChange={(e) =>
                    updateField(thesis.id, "observationEnd", e.target.value)
                  }
                />
              </label>
              <label>
                下一验证事件
                <textarea
                  rows={2}
                  value={thesis.nextValidationEvent}
                  onChange={(e) =>
                    updateField(thesis.id, "nextValidationEvent", e.target.value)
                  }
                  maxLength={FIELD_LIMITS.nextValidationEvent}
                />
              </label>
              <label>
                支持条件
                <textarea
                  rows={2}
                  value={thesis.supportCondition}
                  onChange={(e) =>
                    updateField(thesis.id, "supportCondition", e.target.value)
                  }
                  maxLength={FIELD_LIMITS.supportCondition}
                />
              </label>
              <label>
                反应条件
                <textarea
                  rows={2}
                  value={thesis.falsifier}
                  onChange={(e) =>
                    updateField(thesis.id, "falsifier", e.target.value)
                  }
                  maxLength={FIELD_LIMITS.falsifier}
                />
              </label>
            </div>
            <div className="thesis-editor-tools">
              <span className="draft-origin-label">
                上次编辑：{thesis.lastEditedBy ?? "未编辑"} · 起源：
                {thesis.origin}
              </span>
              <button
                type="button"
                className="prototype-button quiet"
                onClick={() => removeThesis(thesis.id)}
                aria-label={`删除命题 ${thesis.id}`}
              >
                删除此命题
              </button>
            </div>
          </fieldset>
        ))}
        </div>

        <div style={{ display: "flex", gap: 12, marginTop: 12 }}>
          <button type="button" className="prototype-button" onClick={addBlankThesis}>
            ＋ 新增命题
          </button>
          <button
            type="button"
            className="prototype-button primary"
            disabled={submitting}
            onClick={confirmAndCreate}
          >
            {submitting ? "正在创建…" : "确认命题 · 创建研究案例"}
          </button>
        </div>
        {createdCaseId && (
          <p style={{ marginTop: 12 }}>
            ✅ 案例已创建：
            <Link to={`/cases/${createdCaseId}`}>进入案例工作台 →</Link>
          </p>
        )}
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">已有资产</p>
            <h2>可复用的冻结资料</h2>
          </div>
        </div>
        <ul className="case-bullets">
          <li>文档 {view.assets.documentCount} 份（不可变来源层）</li>
          <li>已抽取陈述 {view.assets.statementCount} 条</li>
          <li>指标目录 {view.assets.metricCount} 项</li>
          <li>已复核知识 {view.assets.reviewedLinkCount} 条</li>
        </ul>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">研究计划预览</p>
            <h2>能力探测 · 证据检索 · 结果指标（示例 · 非目标范围）</h2>
          </div>
          <Link to="/plan" className="prototype-next-action">
            查看完整计划 →
          </Link>
        </div>
        <div className="prototype-plan-regions">
          <article className="prototype-plan-region">
            <span className="section-kicker">能力探测</span>
            <h3>{view.plan.providerQueries.length} 项计划</h3>
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {view.plan.providerQueries.map((q) => (
                <li key={q.id} style={{ padding: "4px 0", fontSize: 12 }}>
                  <strong>{q.providerLabel}</strong> · {q.capabilityLabel}
                  <br />
                  <small>
                    {q.modeLabel} · {q.statusLabel} · 截止 {q.cutoff}
                  </small>
                  <br />
                  <small>目的：{q.purpose}</small>
                </li>
              ))}
            </ul>
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">证据检索</span>
            <h3>正面 / 反面</h3>
            <div>
              <strong>正面：</strong>
              <ul>
                {view.plan.positiveEvidenceSearches.map((p) => (
                  <li key={p.id}>
                    {p.label} <small>· {p.scope}</small>
                  </li>
                ))}
              </ul>
              <strong>反面：</strong>
              <ul>
                {view.plan.negativeEvidenceSearches.map((n) => (
                  <li key={n.id}>
                    {n.label} <small>· {n.scope}</small>
                  </li>
                ))}
              </ul>
            </div>
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">结果指标与缺口</span>
            <h3>{view.plan.resultMetrics.length} 项指标</h3>
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {view.plan.resultMetrics.map((m) => (
                <li key={m.id} style={{ padding: "4px 0", fontSize: 12 }}>
                  <strong>{m.name}</strong>
                  <br />
                  <small>
                    {m.value} · {m.period}
                  </small>
                </li>
              ))}
            </ul>
            <h4 style={{ margin: "10px 0 4px", fontSize: 12 }}>缺口</h4>
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {view.plan.gaps.map((g) => (
                <li key={g.id} style={{ padding: "2px 0", fontSize: 12 }}>
                  · {g.label}
                </li>
              ))}
            </ul>
          </article>
        </div>
      </section>
    </div>
  );
}