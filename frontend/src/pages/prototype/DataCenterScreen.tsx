import { useEffect, useState } from "react";
import { researchClient } from "../../data/researchClient";
import type {
  DataCenterView,
  DataMetricSelection,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const STATE_LABEL: Record<string, string> = {
  usable: "截止日可用",
  later: "案例截止日不可用 · 现在已可用",
};

export function DataCenterScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<DataCenterView | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [selection, setSelection] = useState<DataMetricSelection | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getDataCenterView()
      .then((v) => {
        if (!cancelled) {
          setView(v);
          setSelectedId(v.selectedMetricId);
          setSelection({
            selectedMetric: v.selectedMetric,
            series: v.series,
          });
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

  const selectMetric = (catalogId: string) => {
    setSelectedId(catalogId);
    if (!view) return;
    const entry = view.catalog.find((c) => c.id === catalogId);
    if (!entry) return;
    setSelectionLoading(true);
    researchClient
      .getDataCenterMetric(entry.stockId, entry.metricName)
      .then((sel) => setSelection(sel))
      .catch(() => {
        // Selection stays on the last successfully loaded metric.
      })
      .finally(() => setSelectionLoading(false));
  };

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="data-center-loading">
        <p>正在加载数据中心…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="data-center-error">
        <div className="form-error">
          数据中心加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  const selectedMetric = selection?.selectedMetric ?? view.selectedMetric;
  const series = selection?.series ?? view.series;

  return (
    <div className="prototype-screen" data-testid="data-center-screen">
      <header>
        <div className="eyebrow">数据中心 · Data Center</div>
        <h1>指标库 · 时点数据 · 来源确认</h1>
        <p className="lede">
          最新冻结观测 {view.cutoff || "—"}
          {selectionLoading ? " · 加载选中指标…" : ""}
        </p>
      </header>

      <div className="prototype-data-workspace">
        <aside className="metric-catalog">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">指标目录</p>
              <h2>{view.catalog.length} 项</h2>
            </div>
          </div>
          {view.catalog.map((c) => (
            <button
              type="button"
              key={c.id}
              className={`prototype-metric-row ${
                c.id === selectedId ? "is-selected" : ""
              }`}
              onClick={() => selectMetric(c.id)}
            >
              <strong>{c.label}</strong>
              <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                {c.entity} · {c.cadence}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color:
                    c.state.includes("可用") && !c.state.includes("不可")
                      ? "var(--support)"
                      : c.state.includes("权限")
                        ? "var(--contradict)"
                        : "var(--warning)",
                }}
              >
                {c.state}
              </div>
            </button>
          ))}
        </aside>

        <section className="metric-detail">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">指标详情</p>
              <h2>{selectedMetric.name}</h2>
            </div>
            <span className="state-badge reviewed">正式记录</span>
          </div>

          <dl
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: 8,
              margin: 0,
              fontSize: 12,
            }}
          >
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>主体</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.entity}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>期间</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.period}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>取值</dt>
              <dd style={{ margin: 0 }}>
                {selectedMetric.value} {selectedMetric.unit}
              </dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>发布</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.publishedAt}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>可用</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.availableAt}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>获取</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.acquiredAt}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>来源</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.source}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>方法</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.methodology}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>修订</dt>
              <dd style={{ margin: 0 }}>{selectedMetric.revision}</dd>
            </div>
            <div>
              <dt style={{ color: "var(--ink-muted)" }}>Provider Run</dt>
              <dd style={{ margin: 0 }}>
                <code>{selectedMetric.providerRunId}</code>
              </dd>
            </div>
          </dl>

          <p style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 8 }}>
            失败语义：{selectedMetric.failureMeaning}
          </p>

          <h3 style={{ fontSize: 13, marginTop: 16 }}>时序观测</h3>
          <svg
            className="prototype-series-svg"
            viewBox="0 0 600 220"
            role="img"
            aria-label="数据时序"
          >
            {(() => {
              if (series.length === 0) {
                return (
                  <text x={300} y={110} textAnchor="middle" fontSize={12}>
                    该指标暂无冻结观测
                  </text>
                );
              }
              const maxValue = Math.max(...series.map((s) => s.numericValue));
              const minValue = Math.min(...series.map((s) => s.numericValue));
              const range = Math.max(1, maxValue - minValue);
              return (
                <>
                  <line
                    x1={40}
                    y1={20}
                    x2={40}
                    y2={200}
                    stroke="var(--rule)"
                    strokeDasharray="2 4"
                  />
                  <line
                    x1={40}
                    y1={200}
                    x2={580}
                    y2={200}
                    stroke="var(--rule)"
                    strokeDasharray="2 4"
                  />
                  {series.map((p, i) => {
                    const x = 60 + i * 100;
                    const y =
                      200 -
                      ((p.numericValue - minValue) / range) * 160;
                    return (
                      <g key={p.period} className={`series-point ${p.cutoffUsable ? "usable" : "later"}`}>
                        <circle
                          cx={x}
                          cy={y}
                          r={6}
                          stroke="var(--paper)"
                          strokeWidth={2}
                        />
                        <text x={x} y={y - 12} textAnchor="middle">
                          {p.value}
                        </text>
                        <text x={x} y={216} textAnchor="middle" fontSize="10">
                          {p.period}
                        </text>
                      </g>
                    );
                  })}
                </>
              );
            })()}
          </svg>
          <ul style={{ listStyle: "none", padding: 0, fontSize: 11 }}>
            {series.map((p) => (
              <li
                key={p.period}
                style={{
                  padding: "4px 0",
                  borderBottom: "1px solid var(--rule)",
                  color: p.cutoffUsable ? "var(--ink)" : "var(--warning)",
                }}
              >
                <strong>{p.period}</strong> · {p.value} · {STATE_LABEL[p.cutoffUsable ? "usable" : "later"]} · 获取 {p.acquiredAt}
              </li>
            ))}
          </ul>

          <h3 style={{ fontSize: 13, marginTop: 16 }}>来源确认对比</h3>
          <div className="prototype-revision-columns">
            <div>
              <strong>旧来源</strong>
              <p style={{ fontSize: 12, margin: "4px 0" }}>
                取值 {view.revisionComparison.oldValue}
              </p>
              <small>{view.revisionComparison.oldSource}</small>
              <p style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 4 }}>
                {view.revisionComparison.oldCutoffMeaning}
              </p>
            </div>
            <div aria-hidden>→</div>
            <div>
              <strong>新来源</strong>
              <p style={{ fontSize: 12, margin: "4px 0" }}>
                取值 {view.revisionComparison.newValue}
              </p>
              <small>{view.revisionComparison.newSource}</small>
              <p style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 4 }}>
                {view.revisionComparison.newCutoffMeaning}
              </p>
            </div>
          </div>
          <p style={{ fontSize: 12, marginTop: 8 }}>
            为什么重要：{view.revisionComparison.whyItMatters}
          </p>

          <h3 style={{ fontSize: 13, marginTop: 16 }}>研究效能</h3>
          <section
            className="prototype-paper"
            data-testid="research-ops-section"
            aria-label="研究效能指标"
          >
            <p style={{ fontSize: 11, color: "var(--ink-muted)", margin: 0 }}>
              统计时点 {view.researchOps.asOf.slice(0, 10)} ·
              全部指标由不可变账本推导，无自报数字
            </p>

            <h4 style={{ fontSize: 12, margin: "12px 0 4px" }}>审核吞吐</h4>
            <dl
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                gap: 8,
                margin: 0,
                fontSize: 12,
              }}
            >
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>待审链路</dt>
                <dd style={{ margin: 0, fontSize: 16 }}>
                  <strong>{view.researchOps.throughput.pendingLinkReviews}</strong>
                </dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>待审评估</dt>
                <dd style={{ margin: 0, fontSize: 16 }}>
                  <strong>{view.researchOps.throughput.pendingAssessmentReviews}</strong>
                </dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>近 7 天链路复核</dt>
                <dd style={{ margin: 0, fontSize: 16 }}>
                  <strong>{view.researchOps.throughput.linkReviewsLast7d}</strong>
                  <span style={{ color: "var(--ink-muted)", fontSize: 11 }}>
                    {" "}/ 累计 {view.researchOps.throughput.linkReviewsTotal}
                  </span>
                </dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>近 7 天评估复核</dt>
                <dd style={{ margin: 0, fontSize: 16 }}>
                  <strong>{view.researchOps.throughput.assessmentReviewsLast7d}</strong>
                  <span style={{ color: "var(--ink-muted)", fontSize: 11 }}>
                    {" "}/ 累计 {view.researchOps.throughput.assessmentReviewsTotal}
                  </span>
                </dd>
              </div>
            </dl>
            {view.researchOps.throughput.reviewsByReviewer.length > 0 && (
              <p style={{ fontSize: 11, color: "var(--ink-muted)", margin: "6px 0 0" }}>
                按审核人：
                {view.researchOps.throughput.reviewsByReviewer
                  .map((r) => `${r.reviewer} ${r.count}`)
                  .join(" · ")}
              </p>
            )}

            <h4 style={{ fontSize: 12, margin: "12px 0 4px" }}>人机一致率</h4>
            <p style={{ fontSize: 12, margin: 0 }}>
              评估级：
              {view.researchOps.agreement.assessmentAgreementRate !== null
                ? `${Math.round(view.researchOps.agreement.assessmentAgreementRate * 100)}%`
                : "—（暂无复核数据）"}
              <span style={{ color: "var(--ink-muted)", fontSize: 11 }}>
                {view.researchOps.agreement.assessmentOutcomes.length > 0 &&
                  `（${view.researchOps.agreement.assessmentOutcomes
                    .map((o) => `${o.outcome} ${o.count}`)
                    .join(" / ")}）`}
                {view.researchOps.agreement.conclusionChanged > 0 &&
                  ` · 结论被改 ${view.researchOps.agreement.conclusionChanged} 次`}
              </span>
            </p>
            <p style={{ fontSize: 12, margin: "4px 0 0" }}>
              链路级：
              {view.researchOps.agreement.linkAgreementRate !== null
                ? `${Math.round(view.researchOps.agreement.linkAgreementRate * 100)}%`
                : "—（暂无链路复核数据）"}
              <span style={{ color: "var(--ink-muted)", fontSize: 11 }}>
                {view.researchOps.agreement.linkModified > 0 &&
                  ` · 人工改判关系 ${view.researchOps.agreement.linkModified} 条`}
              </span>
            </p>

            <h4 style={{ fontSize: 12, margin: "12px 0 4px" }}>判断时滞（天）</h4>
            <dl
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: 8,
                margin: 0,
                fontSize: 12,
              }}
            >
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>证据 → AI 判断</dt>
                <dd style={{ margin: 0 }}>
                  {view.researchOps.latency.evidenceToAssessmentAvgDays !== null
                    ? `均 ${view.researchOps.latency.evidenceToAssessmentAvgDays} / 峰 ${view.researchOps.latency.evidenceToAssessmentMaxDays}`
                    : "—（暂无评估）"}
                </dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>AI 判断 → 人工复核</dt>
                <dd style={{ margin: 0 }}>
                  {view.researchOps.latency.assessmentToReviewAvgDays !== null
                    ? `均 ${view.researchOps.latency.assessmentToReviewAvgDays} / 峰 ${view.researchOps.latency.assessmentToReviewMaxDays}`
                    : "—（暂无复核）"}
                </dd>
              </div>
            </dl>
          </section>

          <h3 style={{ fontSize: 13, marginTop: 16 }}>计划中的尝试</h3>
          <div className="prototype-paper">
            <strong>{view.plannedAttempt.label}</strong>
            <span className="state-badge ai" style={{ float: "right" }}>
              {view.plannedAttempt.state}
            </span>
            <p style={{ fontSize: 12, marginTop: 4 }}>
              {view.plannedAttempt.meaning}
            </p>
          </div>

          <h3 style={{ fontSize: 13, marginTop: 16 }}>历史运行</h3>
          <div className="prototype-provider-run-grid">
            {view.historicalRuns.map((r) => (
              <article
                key={r.id}
                className={`prototype-provider-run ${r.outcome === "success" ? "success" : r.outcome === "quota_failure" ? "quota" : "permission"}`}
              >
                <strong>{r.providerLabel}</strong>
                <span
                  className="state-badge"
                  style={{
                    float: "right",
                    color:
                      r.outcome === "success"
                        ? "var(--support)"
                        : r.outcome === "quota_failure"
                          ? "var(--warning)"
                          : "var(--contradict)",
                  }}
                >
                  {r.outcomeLabel}
                </span>
                <p style={{ fontSize: 11, margin: "4px 0" }}>{r.detailLabel}</p>
                <small>{r.observedAt}</small>
              </article>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}