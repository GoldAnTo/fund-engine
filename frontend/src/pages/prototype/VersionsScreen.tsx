import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  CaseSummaryItem,
  VersionColumnContent,
  VersionsView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const CONCLUSION_LABELS: Record<string, string> = {
  supported: "支持",
  insufficient_evidence: "证据不足",
  refuted: "反驳",
  contested: "争议",
  pending: "待评估",
};
function conclusionLabel(c: string): string {
  return CONCLUSION_LABELS[c] ?? c;
}
function badgeFor(c: string): string {
  if (c === "supported") return "reviewed";
  if (c === "insufficient_evidence") return "monitoring";
  if (c === "refuted") return "rejected";
  if (c === "contested") return "pending";
  return "pending";
}

function renderColumn(label: string, content: VersionColumnContent, accent: string) {
  return (
    <article className="version-column" data-snapshot={accent}>
      <p className="section-kicker">{label}</p>
      <h3>正式判断 · {content.formalConclusion.state}</h3>
      <p style={{ fontSize: 12 }}>{content.formalConclusion.text}</p>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>输入</h4>
      <ul className="prototype-version-record-list">
        {content.inputs.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>
              {row.label} · {row.version ?? "—"}
            </span>
          </li>
        ))}
      </ul>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>关系</h4>
      <ul className="prototype-version-record-list compact">
        {content.relationships.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>{row.label}</span>
            <em>{row.role}</em>
          </li>
        ))}
      </ul>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>因素</h4>
      <ul className="prototype-version-record-list">
        {content.factors.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>
              {row.label} · {row.role}
            </span>
          </li>
        ))}
      </ul>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>缺口</h4>
      <ul className="prototype-version-record-list">
        {content.gaps.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>
              {row.label} · {row.state}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function VersionsScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<VersionsView | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [rerunNotice, setRerunNotice] = useState<string | null>(null);
  const [rerunSucceeded, setRerunSucceeded] = useState(false);
  const [cases, setCases] = useState<CaseSummaryItem[]>([]);
  const [caseId, setCaseId] = useState<string>("");
  const [baseCutoff, setBaseCutoff] = useState<string>("");
  const [compareCutoff, setCompareCutoff] = useState<string>("");

  const loadView = useCallback(
    (id?: string, opts?: { base?: string; compare?: string }) =>
      researchClient.getVersionsView(id, opts).then((v) => {
        setView(v);
        if (!opts?.base) setBaseCutoff(v.beforeSnapshot.cutoff);
        if (!opts?.compare) setCompareCutoff(v.afterSnapshot.cutoff);
        return v;
      }),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      researchClient.listCaseSummaries().catch(() => []),
      loadView(caseId || undefined),
    ])
      .then(([list]) => {
        if (!cancelled) {
          setCases(list);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadView, caseId]);

  const runRerun = () => {
    if (!view?.focusThesisId || rerunning) return;
    setRerunning(true);
    setRerunNotice(null);
    setRerunSucceeded(false);
    researchClient
      .rerunThesis(view.focusThesisId)
      .then((result) => {
        setRerunNotice(
          `已冻结新快照 ${result.snapshotId.slice(0, 8)}，结论：${result.conclusion}（临时评估，未经人工复核）`,
        );
        setRerunSucceeded(true);
        return loadView();
      })
      .catch((err: Error) => {
        // 422 = 合规拒绝：AI 文本被拦截，这是状态而不是故障。
        setRerunNotice(`AI RERUN 未完成：${err.message || "未知错误"}`);
      })
      .finally(() => setRerunning(false));
  };

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="versions-loading">
        <p>正在加载版本比较…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="versions-error">
        <div className="form-error">
          版本比较加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen" data-testid="versions-screen">
      <header>
        <div className="eyebrow">监测与更新 · Versions</div>
        <h1>快照版本比较</h1>
        <p className="lede">
          案例{" "}
          <Link to={`/cases/${view.case.id}`}>
            <code>{view.case.id}</code>
          </Link>{" "}
          · {view.case.title}
        </p>
        {cases.length > 1 ? (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              marginTop: 8,
              fontSize: 12,
            }}
          >
            <label htmlFor="versions-case">切换案例：</label>
            <select
              id="versions-case"
              value={caseId}
              onChange={(e) => setCaseId(e.target.value)}
            >
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title} · {c.topic}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {view.availableCutoffs.length > 0 ? (
          <div
            data-testid="version-snapshot-pickers"
            style={{
              display: "flex",
              gap: 16,
              alignItems: "center",
              marginTop: 8,
              fontSize: 12,
              flexWrap: "wrap",
            }}
          >
            <label>
              基准快照：&nbsp;
              <select
                value={baseCutoff}
                onChange={(e) => {
                  const newBase = e.target.value;
                  setBaseCutoff(newBase);
                  loadView(caseId || undefined, {
                    base: newBase,
                    compare: compareCutoff,
                  });
                }}
              >
                <option value="1970-01-01T00:00:00Z">（最早 / 起点）</option>
                {view.availableCutoffs.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <span>→</span>
            <label>
              当前快照：&nbsp;
              <select
                value={compareCutoff}
                onChange={(e) => {
                  const newCompare = e.target.value;
                  setCompareCutoff(newCompare);
                  loadView(caseId || undefined, {
                    base: baseCutoff,
                    compare: newCompare,
                  });
                }}
              >
                {view.availableCutoffs.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <span style={{ color: "var(--ink-muted)" }}>
              （共 {view.availableCutoffs.length} 个快照点可对比）
            </span>
          </div>
        ) : null}
      </header>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">快照</p>
            <h2>
              {view.beforeSnapshot.id} ({view.beforeSnapshot.cutoff}) →{" "}
              {view.afterSnapshot.id} ({view.afterSnapshot.cutoff})
            </h2>
          </div>
          <span className="state-badge reviewed">
            冻结于 {view.afterSnapshot.freezeTime}
          </span>
        </div>

        <div className="prototype-version-compare">
          {renderColumn(
            `上一季度 · ${view.beforeSnapshot.id}`,
            view.before,
            "before",
          )}
          <aside className="change-rail" aria-label="变更说明">
            <p className="section-kicker">变更记录</p>
            <h3>从 {view.beforeSnapshot.cutoff} 到 {view.afterSnapshot.cutoff}</h3>
            <ul className="prototype-version-record-list compact">
              <li>
                <strong>输入</strong>
                <span>{view.changeRail.inputSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>关系</strong>
                <span>{view.changeRail.relationshipSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>因素</strong>
                <span>{view.changeRail.factorSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>结论</strong>
                <span>{view.changeRail.conclusionSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>缺口</strong>
                <span>{view.changeRail.gapSummary}</span>
                <em></em>
              </li>
            </ul>
            <p style={{ fontSize: 12, marginTop: 8 }}>
              {view.changeRail.rationale}
            </p>
            <p style={{ fontSize: 11, color: "var(--ink-muted)" }}>
              审核人：{view.changeRail.reviewedBy} · {view.changeRail.reviewedAt}
            </p>
            <div className="prototype-version-ai-proposal">
              <strong>AI 提议（未经人工复核）</strong>
              <p style={{ fontSize: 12 }}>{view.aiProposal.text}</p>
              <small>
                Run {view.aiProposal.runId} · {view.aiProposal.observedAt}
              </small>
              <p style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                边界：{view.aiProposal.boundary}
              </p>
              <button
                type="button"
                className="prototype-button primary"
                disabled={rerunning || !view.focusThesisId}
                onClick={runRerun}
                data-testid="ai-rerun-button"
              >
                {rerunning ? "RERUN 执行中…" : "AI RERUN（冻结新快照）"}
              </button>
              {rerunNotice ? (
                <p style={{ fontSize: 11, marginTop: 6 }} role="status">
                  {rerunNotice}
                  {rerunSucceeded ? (
                    <>
                      {" "}
                      <Link to={`/cases/${view.case.id}`}>
                        回案例工作台查看 →
                      </Link>
                    </>
                  ) : null}
                </p>
              ) : null}
            </div>
          </aside>
          {renderColumn(
            `当前快照 · ${view.afterSnapshot.id}`,
            view.after,
            "after",
          )}
        </div>
      </section>

      <section style={{ marginTop: 32 }}>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">多命题概览</p>
            <h2>本轮快照内全部命题的 before → after 变化</h2>
          </div>
        </div>
        {view.perThesisChanges.length === 0 ? (
          <p>（本案例当前没有可对比的命题）</p>
        ) : (
          <table
            className="prototype-table"
            data-testid="version-thesis-changes"
            style={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: 6 }}>命题</th>
                <th style={{ textAlign: "left", padding: 6 }}>结论 before</th>
                <th style={{ textAlign: "left", padding: 6 }}>结论 after</th>
                <th style={{ textAlign: "right", padding: 6 }}>缺口 before</th>
                <th style={{ textAlign: "right", padding: 6 }}>缺口 after</th>
                <th style={{ textAlign: "right", padding: 6 }}>新增关系</th>
                <th style={{ textAlign: "right", padding: 6 }}>移除关系</th>
              </tr>
            </thead>
            <tbody>
              {view.perThesisChanges.map((t) => (
                <tr key={t.thesisId} data-testid="version-thesis-row">
                  <td style={{ padding: 6, maxWidth: 320 }}>
                    <Link
                      to={`/cases/${view.case.id}?thesis_id=${t.thesisId}`}
                      style={{ fontSize: 12, color: "var(--accent)" }}
                    >
                      {t.statement}
                    </Link>
                  </td>
                  <td style={{ padding: 6 }}>
                    {t.conclusionBefore ? (
                      <span className={`state-badge ${badgeFor(t.conclusionBefore)}`}>
                        {conclusionLabel(t.conclusionBefore)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--ink-muted)", fontSize: 12 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: 6 }}>
                    {t.conclusionAfter ? (
                      <span className={`state-badge ${badgeFor(t.conclusionAfter)}`}>
                        {conclusionLabel(t.conclusionAfter)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--ink-muted)", fontSize: 12 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: 6, textAlign: "right" }}>{t.gapsBeforeCount}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>{t.gapsAfterCount}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    <strong style={{ color: "var(--positive)" }}>+{t.addedLinks}</strong>
                  </td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    {t.removedLinks > 0 ? (
                      <strong style={{ color: "var(--negative)" }}>−{t.removedLinks}</strong>
                    ) : (
                      <span style={{ color: "var(--ink-muted)" }}>0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}