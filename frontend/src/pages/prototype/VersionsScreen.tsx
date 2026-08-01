import { useEffect, useState } from "react";
import { researchClient } from "../../data/researchClient";
import type { VersionColumnContent, VersionsView } from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
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

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getVersionsView()
      .then((v) => {
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
  }, []);

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
          案例 <code>{view.case.id}</code> · {view.case.title}
        </p>
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
            </div>
          </aside>
          {renderColumn(
            `当前快照 · ${view.afterSnapshot.id}`,
            view.after,
            "after",
          )}
        </div>
      </section>
    </div>
  );
}