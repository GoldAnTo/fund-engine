import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  CaseSummaryItem,
  ResearchPlanView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const TYPE_LABEL: Record<string, string> = {
  document: "冻结文档",
  statement: "来源陈述",
  metric: "结果数据",
  evidence_link: "已审核关系",
};

export function ResearchPlanScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<ResearchPlanView | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [ingestNotice, setIngestNotice] = useState<string | null>(null);
  const [ingestSucceeded, setIngestSucceeded] = useState(false);
  const [cases, setCases] = useState<CaseSummaryItem[]>([]);
  const [caseId, setCaseId] = useState<string>("");
  // 宏观时序查询输入（每行一条 MacroIndustryData 查询），不填则只跑原有
  // 默认研报/公告/新闻/行情。"碳酸锂价格走势"等缺口可在 UI 一键补齐。
  const [macroQueriesText, setMacroQueriesText] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      researchClient.listCaseSummaries().catch(() => []),
      researchClient.getResearchPlanView(caseId || undefined),
    ])
      .then(([list, v]) => {
        if (!cancelled) {
          setCases(list);
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
  }, [caseId]);

  // 触发引擎 ingest 步骤：从 Gildata 拉取研报/公告/新闻/行情/宏观时序
  // 并写入台账（幂等：内容哈希 + 自然键双层去重）。
  const runIngest = () => {
    if (!view || ingesting) return;
    setIngesting(true);
    setIngestNotice(null);
    setIngestSucceeded(false);
    const macroQueries = macroQueriesText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    researchClient
      .ingestDocuments(view.case.id, { macroQueries })
      .then((r) => {
        const total =
          r.researchReports + r.announcements + r.news + r.macroSeries;
        setIngestNotice(
          total > 0
            ? `接入完成：研报 ${r.researchReports} · 公告 ${r.announcements} · 新闻 ${r.news} · 宏观时序 ${r.macroSeries} · 片段 ${r.spans} · 估值写入 ${r.valuationsWritten}（跳过重复 ${r.valuationsSkipped}）。`
            : "接入完成：没有新文档（全部去重跳过，或离线模式未接数据源）。",
        );
        setIngestSucceeded(total > 0);
      })
      .catch((err: Error) => {
        setIngestNotice(`数据接入未完成：${err.message || "未知错误"}`);
      })
      .finally(() => setIngesting(false));
  };

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="plan-loading">
        <p>正在加载研究计划…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="plan-error">
        <div className="form-error">
          研究计划加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen" data-testid="plan-screen">
      <header className="plan-header">
        <div>
          <div className="eyebrow">研究计划与证据获取</div>
          <h1>{view.case.id}</h1>
          <p className="lede">
            同一案例内复用、扩展、获取、审核与接口；
            所有操作仅改变本页类型所反映的状态。
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
              <label htmlFor="plan-case">切换案例：</label>
              <select
                id="plan-case"
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
        </div>
        <div>
          <button
            type="button"
            className="prototype-button primary"
            disabled={ingesting}
            onClick={runIngest}
            data-testid="ingest-button"
          >
            {ingesting ? "接入中…" : "⇪ 接入数据"}
          </button>
          {ingestNotice ? (
            <p style={{ fontSize: 12, marginTop: 6, maxWidth: 360 }} role="status">
              {ingestNotice}
              {ingestSucceeded ? (
                <>
                  {" "}
                  <Link to="/library">去资料库查看 →</Link>
                </>
              ) : null}
            </p>
          ) : null}
        </div>
      </header>

      <section className="plan-meta-grid" aria-label="计划元数据">
        <MetaCell label="ResearchCase" value={view.case.id} mono />
        <MetaCell label="研究期间" value={view.case.researchPeriod} />
        <MetaCell label="证据截止" value={view.case.cutoff} />
        <MetaCell label="计划修订" value={view.case.revision} mono />
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">现有资料</p>
            <h2>已纳入 {view.existingAssets.length} 项</h2>
          </div>
          <span className="state-badge reviewed">
            按类型排序 · 每页 {view.assetPageSize}
          </span>
        </div>
        <div className="prototype-plan-asset-list">
          {view.orderedAssets.map((asset) => (
            <article
              key={asset.id}
              className="prototype-plan-asset"
              style={{
                borderColor: asset.selected ? "var(--selected-border)" : undefined,
                background: asset.selected ? "var(--selected-surface)" : undefined,
              }}
            >
              <div>
                <strong>{asset.label}</strong>
                <br />
                <small>
                  {asset.sourceVersion} · {asset.sourceSpan}
                </small>
                <br />
                <small>
                  {asset.kind === "metric"
                    ? `${asset.metricName ?? ""} · ${asset.metricValue ?? ""} · ${asset.metricPeriod ?? ""}`
                    : ""}
                </small>
              </div>
              <span
                className="type-label"
                style={{
                  color:
                    asset.reviewState === "reviewed"
                      ? "var(--support)"
                      : "var(--warning)",
                }}
              >
                {TYPE_LABEL[asset.kind]} · {asset.reviewState === "reviewed" ? "已审核" : "待审核"} · ×{asset.reviewCount}
              </span>
            </article>
          ))}
        </div>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">研究能力探测（示例 · 非目标范围）</p>
            <h2>{view.providerQueries.length} 项待执行查询</h2>
          </div>
          <span className="state-badge ai">能力探测阶段</span>
        </div>
        <div className="prototype-plan-regions">
          {view.providerQueries.map((q) => (
            <article key={q.id} className="prototype-plan-region">
              <span className="section-kicker">{q.provider}</span>
              <h3>{q.capability}</h3>
              <p style={{ fontSize: 12, marginTop: 6 }}>{q.purpose}</p>
              <dl style={{ display: "grid", gap: 4, margin: 0, fontSize: 11 }}>
                <div>
                  <dt style={{ color: "var(--ink-muted)" }}>日期范围</dt>
                  <dd style={{ margin: 0 }}>
                    {q.dateScope.start} 至 {q.dateScope.end}
                  </dd>
                </div>
                <div>
                  <dt style={{ color: "var(--ink-muted)" }}>截止</dt>
                  <dd style={{ margin: 0 }}>{q.cutoff}</dd>
                </div>
                <div>
                  <dt style={{ color: "var(--ink-muted)" }}>期望产物</dt>
                  <dd style={{ margin: 0 }}>{q.intendedArtifact}</dd>
                </div>
                <div>
                  <dt style={{ color: "var(--ink-muted)" }}>状态</dt>
                  <dd style={{ margin: 0 }}>
                    <span className="state-badge ai">{q.status}</span>{" "}
                    <small>{q.exposureStatus}</small>
                  </dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">采集 / 复用 / 阻塞（示例 · 非目标范围）</p>
            <h2>资料流向分桶</h2>
          </div>
        </div>
        <div className="prototype-plan-regions">
          <article className="prototype-plan-region">
            <span className="section-kicker">已复用并冻结</span>
            <h3>{view.collection.reused.length} 项</h3>
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {view.collection.reused.map((r) => (
                <li key={r.id} style={{ padding: "4px 0", fontSize: 12 }}>
                  · {r.label}
                  <br />
                  <small>截止 {r.cutoff}</small>
                </li>
              ))}
            </ul>
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">等待能力探测</span>
            <h3>{view.collection.awaitingProbe.length} 项</h3>
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {view.collection.awaitingProbe.map((r) => (
                <li key={r.id} style={{ padding: "4px 0", fontSize: 12 }}>
                  · {r.label}
                </li>
              ))}
            </ul>
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">权限阻塞</span>
            <h3>{view.collection.blocked.length} 项</h3>
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {view.collection.blocked.map((r) => (
                <li key={r.id} style={{ padding: "4px 0", fontSize: 12 }}>
                  · {r.label}
                </li>
              ))}
            </ul>
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">正在获取</span>
            <h3>{view.collection.running.length} 项</h3>
            {view.collection.running.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--ink-muted)" }}>暂无运行中任务。</p>
            ) : (
              <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
                {view.collection.running.map((r) => (
                  <li key={r.id} style={{ padding: "4px 0", fontSize: 12 }}>
                    · {r.label}
                  </li>
                ))}
              </ul>
            )}
          </article>
        </div>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">待核实条目</p>
            <h2>{view.pendingResults.length} 项等待审核</h2>
          </div>
          <span className="state-badge warning">需人工</span>
        </div>
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {view.pendingResults.map((p) => (
            <li
              key={p.id}
              className="prototype-plan-failure-row"
              style={{ borderColor: "var(--warning)" }}
            >
              <strong>{p.targetLabel}</strong>
              <span style={{ float: "right" }} className="state-badge warning">
                {p.reviewLabel}
              </span>
              <small>{p.task}</small>
              <small>
                {p.sourceId} · {p.sourceVersion}
              </small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">缺口</p>
            <h2>{view.gaps.length} 项待补</h2>
          </div>
        </div>
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {view.gaps.map((g) => (
            <li key={g.id} className="prototype-plan-failure-row">
              <strong>[{g.type}] {g.label}</strong>
              <small>{g.scope}</small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">结果指标（示例 · 非目标范围）</p>
            <h2>研究计划预定 {view.resultMetrics.length} 项数据采集</h2>
          </div>
        </div>
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {view.resultMetrics.map((m) => (
            <li key={m.id} className="prototype-plan-failure-row">
              <strong>{m.name}</strong>
              <small>
                {m.value} · {m.period}
              </small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">失败 / 额度与权限 / 上传材料</p>
            <h2>资料缺口透明化</h2>
          </div>
        </div>
        <div className="prototype-plan-regions plan-failure-triplet">
          <article className="prototype-plan-region">
            <span className="section-kicker">失败运行</span>
            <h3>{view.failures.length} 项</h3>
            {view.failures.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--ink-muted)" }}>暂无失败运行。</p>
            ) : (
              view.failures.map((r) => (
                <div key={r.id} className="prototype-plan-failure-row" style={{ borderColor: "var(--contradict)" }}>
                  <strong>{r.provider} · {r.outcome}</strong>
                  <small>{r.detail}</small>
                  <small>{r.observedAt}</small>
                </div>
              ))
            )}
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">额度与权限</span>
            <h3>{view.permissionGaps.length} 项</h3>
            {view.permissionGaps.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--ink-muted)" }}>暂无权限缺口。</p>
            ) : (
              view.permissionGaps.map((r) => (
                <div key={r.id} className="prototype-plan-failure-row" style={{ borderColor: "var(--ai-draft)" }}>
                  <strong>{r.provider} · {r.outcome}</strong>
                  <small>{r.detail}</small>
                  <small>{r.observedAt}</small>
                </div>
              ))
            )}
          </article>
          <article className="prototype-plan-region">
            <span className="section-kicker">人工补录</span>
            <h3>{view.manualUploads.length} 项</h3>
            {view.manualUploads.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--ink-muted)" }}>尚无人工补录。</p>
            ) : (
              view.manualUploads.map((r) => (
                <div key={r.id} className="prototype-plan-failure-row" style={{ borderColor: "var(--ai-draft)" }}>
                  <strong>{r.provider}</strong>
                  <small>{r.detail}</small>
                  <small>{r.observedAt} · {r.sourceVersion}</small>
                </div>
              ))
            )}
          </article>
        </div>
      </section>
    </div>
  );
}

function MetaCell({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="meta-cell">
      <span>{label}</span>
      <strong style={mono ? { fontFamily: "ui-monospace, monospace" } : undefined}>
        {value}
      </strong>
    </div>
  );
}