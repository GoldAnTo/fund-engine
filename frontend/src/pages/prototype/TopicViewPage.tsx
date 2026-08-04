import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  TopicCaseView,
  TopicThesisView,
  TopicView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const AI_VARIANT: Record<string, "ai" | "support" | "contradict" | "warning"> = {
  supported: "support",
  contradicted: "contradict",
  insufficient_evidence: "warning",
  pending: "ai",
};

const AI_LABEL: Record<string, string> = {
  supported: "支持",
  contradicted: "反证",
  insufficient_evidence: "证据不足",
  pending: "未评估",
};

const REVIEW_VARIANT: Record<
  string,
  "reviewed" | "warning" | "ai" | "support" | "contradict" | "draft"
> = {
  confirmed: "reviewed",
  modified: "warning",
  rejected: "contradict",
};

const REVIEW_LABEL: Record<string, string> = {
  confirmed: "已确认",
  modified: "已修正",
  rejected: "已驳回",
};

export function TopicViewPage() {
  const params = useParams<{ tag?: string }>();
  const tag = params.tag ?? "";
  const [searchParams, setSearchParams] = useSearchParams();
  const cutoff = searchParams.get("cutoff") ?? undefined;
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<TopicView | null>(null);

  useEffect(() => {
    if (!tag) {
      setState({ kind: "error", message: "缺少主题标签" });
      return;
    }
    let cancelled = false;
    setState({ kind: "loading" });
    researchClient
      .getThemeView(tag, cutoff ? { cutoff } : undefined)
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
  }, [tag, cutoff]);

  function setCutoff(value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("cutoff", value);
    else next.delete("cutoff");
    setSearchParams(next, { replace: true });
  }

  const thesisCountsByCase = useMemo<Record<string, number>>(() => {
    if (!view) return {};
    const out: Record<string, number> = {};
    for (const c of view.cases) {
      out[c.caseId] = c.theses.length;
    }
    return out;
  }, [view]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="topic-view-loading">
        <p>正在加载主题视图…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="topic-view-error">
        <div className="form-error">
          主题视图加载失败：{state.message ?? "未知错误"}
        </div>
        <p>
          <Link to="/topics">← 返回主题列表</Link>
        </p>
      </div>
    );
  }

  const emptyTopic =
    view.cases.length === 0 &&
    view.companyRoles.length === 0 &&
    view.fundExposure.length === 0;

  return (
    <div
      className="prototype-screen topic-view-screen"
      data-testid="topic-view-screen"
    >
      <PageHeader
        title={`${view.tag} · 主题视图`}
        eyebrow="主题研究 · 跨案例聚合投影"
        lede="所有数字均可展开到 derived_from 明细。本视图是案例层判断的聚合投影，不构成主题级结论。"
        meta={
          <dl className="theme-meta-grid">
            <MetaCell label="案例" value={String(view.cases.length)} />
            <MetaCell
              label="公司"
              value={String(
                new Set(view.companyRoles.map((r) => r.companyId)).size,
              )}
            />
            <MetaCell
              label="命题"
              value={String(view.derivedFrom.thesisIds.length)}
            />
            <MetaCell
              label="持仓行"
              value={String(view.fundExposure.length)}
            />
            <MetaCell
              label="证据截止"
              value={view.cutoff.slice(0, 10)}
              mono
            />
            <MetaCell
              label="历史回放"
              value={view.isHistorical ? "是" : "否"}
              warn={view.isHistorical}
            />
          </dl>
        }
        actions={
          <>
            <Link className="prototype-button" to="/topics">
              ← 主题列表
            </Link>
            {cutoff ? (
              <button
                type="button"
                className="prototype-button"
                onClick={() => setCutoff(null)}
              >
                回到当下
              </button>
            ) : (
              <button
                type="button"
                className="prototype-button"
                onClick={() => setCutoff("2025-06-30T00:00:00+08:00")}
              >
                切到 2025-06-30 回放
              </button>
            )}
          </>
        }
      />

      <PaperCard data-testid="topic-view-banner">
        <p style={{ fontSize: 12, margin: 0 }}>
          ⚠ 主题视图不存储任何主题级结论——所有数字均可展开到 derived_from
          明细并继续下钻到案例层 AIAssessment / ReviewDecision / SourceStatement。
        </p>
      </PaperCard>

      {emptyTopic && (
        <PaperCard data-testid="topic-view-empty">
          <p className="muted">
            该主题标签当前无参与案例、公司角色或基金披露持仓。
            可在 <Link to="/themes">主题列表</Link> 中进入具体案例后挂接主题标签。
          </p>
        </PaperCard>
      )}

      <div className="topic-view__columns">
        <section className="topic-view__main">
          <PaperCard data-testid="topic-view-cases">
            <p className="section-kicker">
              参与案例与命题有效状态 ({view.cases.length})
            </p>
            {view.cases.length === 0 ? (
              <p className="muted">无参与案例</p>
            ) : (
              <ul className="topic-case-list">
                {view.cases.map((c) => (
                  <CaseMatrix key={c.caseId} c={c} />
                ))}
              </ul>
            )}
          </PaperCard>

          <PaperCard data-testid="topic-view-roles">
            <p className="section-kicker">
              公司 × 主题角色 ({view.companyRoles.length})
            </p>
            {view.companyRoles.length === 0 ? (
              <p className="muted">无公司角色记录</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>公司</th>
                    <th>案例</th>
                    <th>角色</th>
                    <th>适用期间</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {view.companyRoles.map((r, i) => (
                    <tr
                      key={`${r.companyId}-${r.caseId ?? "na"}-${i}`}
                      data-testid={`topic-role-row-${r.companyId}-${i}`}
                    >
                      <td>
                        <Link
                          to={`/companies/${encodeURIComponent(r.companyId)}`}
                          className="prototype-link"
                        >
                          {r.companyName}
                        </Link>
                        <br />
                        <span className="muted" style={{ fontSize: 11 }}>
                          {r.companyCode}
                        </span>
                      </td>
                      <td>
                        {r.caseTitle ?? r.caseId ?? "—"}
                      </td>
                      <td>{r.role}</td>
                      <td>
                        {r.applicableFrom ?? "—"} 至 {r.applicableTo ?? "至今"}
                      </td>
                      <td>
                        {r.statementId ? (
                          <span className="muted" style={{ fontSize: 11 }}>
                            statement {r.statementId.slice(0, 8)}
                          </span>
                        ) : (
                          <span className="muted">无来源记录</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          <PaperCard data-testid="topic-view-exposure">
            <p className="section-kicker">
              基金披露持仓构成 ({view.fundExposure.length})
            </p>
            {view.fundExposure.length === 0 ? (
              <p className="muted">无基金披露持仓</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>基金</th>
                    <th>股票</th>
                    <th>权重</th>
                    <th>报告期</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {view.fundExposure.map((p, i) => (
                    <tr
                      key={`${p.fundId}-${p.stockId}-${p.reportPeriod}-${i}`}
                    >
                      <td>
                        {p.fundName}
                        <br />
                        <span className="muted" style={{ fontSize: 11 }}>
                          {p.fundCode}
                        </span>
                      </td>
                      <td>
                        {p.stockName}
                        <br />
                        <span className="muted" style={{ fontSize: 11 }}>
                          {p.stockCode}
                        </span>
                      </td>
                      <td>{p.weight.toFixed(2)}%</td>
                      <td>{p.reportPeriod}</td>
                      <td>
                        <span className="muted" style={{ fontSize: 11 }}>
                          {p.source}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {view.fundExposure.length > 0 && (
              <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
                权重口径为数据源原始定义（占流通 A 股 / 占净值并存），不强制统一。
                全部持仓行汇总在 view.derived_from.disclosure_ids。
              </p>
            )}
          </PaperCard>
        </section>

        <aside className="topic-view__side">
          <PaperCard data-testid="topic-view-derived">
            <p className="section-kicker">derived_from</p>
            <DerivedSection
              label="案例 IDs"
              ids={view.derivedFrom.caseIds}
            />
            <DerivedSection
              label="命题 IDs"
              ids={view.derivedFrom.thesisIds}
            />
            <DerivedSection
              label="主题角色 IDs"
              ids={view.derivedFrom.themeRoleIds}
            />
            <DerivedSection
              label="披露行 IDs"
              ids={view.derivedFrom.disclosureIds}
            />
          </PaperCard>
          {thesisCountsByCase && Object.keys(thesisCountsByCase).length > 0 && (
            <PaperCard>
              <p className="section-kicker">每案例命题数</p>
              <ul style={{ paddingLeft: 16, fontSize: 12 }}>
                {Object.entries(thesisCountsByCase).map(([cid, n]) => (
                  <li key={cid}>
                    {cid.slice(0, 12)}… · {n}
                  </li>
                ))}
              </ul>
            </PaperCard>
          )}
        </aside>
      </div>
    </div>
  );
}

function CaseMatrix({ c }: { c: TopicCaseView }) {
  return (
    <li
      data-testid={`topic-case-row-${c.caseId}`}
      style={{ marginBottom: 12 }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <strong>{c.caseTitle}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          · {c.caseId}
        </span>
      </div>
      <div
        style={{
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          margin: "4px 0 8px",
        }}
      >
        {Object.entries(c.thesisCounts).map(([k, v]) =>
          v > 0 ? (
            <span
              key={k}
              className={`status-pill status-pill--${
                k === "supported"
                  ? "support"
                  : k === "contradicted"
                    ? "contradict"
                    : k === "insufficient_evidence" || k === "ai_pending"
                      ? "ai"
                      : k === "rejected"
                        ? "contradict"
                        : "warning"
              }`}
              data-testid={`topic-case-count-${c.caseId}-${k}`}
            >
              {k} {v}
            </span>
          ) : null,
        )}
      </div>
      <ul className="topic-thesis-list" style={{ paddingLeft: 16 }}>
        {c.theses.map((t) => (
          <ThesisRow key={t.thesisId} t={t} />
        ))}
      </ul>
    </li>
  );
}

function ThesisRow({ t }: { t: TopicThesisView }) {
  const evidence = t.evidence ?? [];
  return (
    <li style={{ marginBottom: 12 }}>
      <div>
        <span style={{ fontSize: 12 }}>{t.title ?? t.statement.slice(0, 36)}</span>
        <span style={{ marginLeft: 6, display: "inline-flex", gap: 4 }}>
          <StatusBadge
            variant={
              t.aiConclusion ? AI_VARIANT[t.aiConclusion] ?? "ai" : "ai"
            }
          >
            AI · {t.aiConclusion ? AI_LABEL[t.aiConclusion] : "未评估"}
            {t.aiProvisional ? " · 草案" : ""}
          </StatusBadge>
          {t.reviewOutcome ? (
            <StatusBadge
              variant={REVIEW_VARIANT[t.reviewOutcome] ?? "reviewed"}
            >
              人工 · {REVIEW_LABEL[t.reviewOutcome] ?? t.reviewOutcome}
            </StatusBadge>
          ) : (
            <StatusBadge variant="ai">人工 · 待复核</StatusBadge>
          )}
        </span>
      </div>
      <div className="topic-evidence-summary">
        {Object.entries(t.evidenceCounts ?? {}).map(([role, count]) => (
          count > 0 ? <span key={role}>{evidenceRoleLabel(role)} {count}</span> : null
        ))}
      </div>
      {evidence.length > 0 ? (
        <details className="topic-evidence-details" data-testid={`topic-evidence-${t.thesisId}`}>
          <summary>展开证据（{evidence.length} 条）</summary>
          <div className="topic-evidence-list">
            {evidence.map((item) => <EvidenceItem key={item.linkId} item={item} />)}
          </div>
        </details>
      ) : (
        <p className="muted" style={{ fontSize: 11 }}>暂无可见证据</p>
      )}
    </li>
  );
}

function evidenceRoleLabel(role: string): string {
  return role === "supports" ? "支持" : role === "contradicts" ? "反向" : role === "contextualizes" ? "背景" : role;
}

function EvidenceItem({ item }: { item: NonNullable<TopicThesisView["evidence"]>[number] }) {
  const scope = item.scope;
  const status = typeof scope.evidence_status === "string" ? scope.evidence_status : "未标注";
  const period = typeof scope.period === "string" ? scope.period : "时点未标注";
  const sourceType = typeof scope.source === "string" ? scope.source : "来源类型未标注";
  const missing = Array.isArray(scope.missing) ? scope.missing.filter((v): v is string => typeof v === "string") : [];
  return (
    <article className="topic-evidence-item">
      <div className="topic-evidence-item__head">
        <strong>{evidenceRoleLabel(item.role)}</strong>
        <span className="muted">{period} · {status}</span>
        <span className="muted">审核：{item.reviewState}</span>
      </div>
      <p>{item.statement}</p>
      <dl className="topic-evidence-meta">
        <div><dt>来源</dt><dd>{item.sourceUrl ?? "冻结来源（无外链）"}</dd></div>
        <div><dt>来源类型</dt><dd>{sourceType}</dd></div>
        <div><dt>原文定位</dt><dd>{formatLocator(item.locator)}</dd></div>
      </dl>
      {missing.length > 0 && <p className="topic-evidence-missing">待补证据：{missing.join("、")}</p>}
    </article>
  );
}

function formatLocator(locator: Record<string, unknown>): string {
  const parts = Object.entries(locator).slice(0, 3).map(([key, value]) => `${key}=${String(value)}`);
  return parts.length > 0 ? parts.join(" · ") : "定位未标注";
}

function DerivedSection({ label, ids }: { label: string; ids: string[] }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <p className="muted" style={{ fontSize: 11, margin: 0 }}>
        {label} ({ids.length})
      </p>
      {ids.length === 0 ? (
        <p className="muted" style={{ fontSize: 11, margin: 0 }}>
          —
        </p>
      ) : (
        <p style={{ fontSize: 11, margin: 0, wordBreak: "break-all" }}>
          {ids.join("、")}
        </p>
      )}
    </div>
  );
}

function MetaCell({
  label,
  value,
  mono = false,
  warn = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <div className="theme-meta-grid__cell">
      <dt>{label}</dt>
      <dd
        style={{
          fontFamily: mono ? "var(--font-mono, monospace)" : undefined,
          color: warn ? "var(--warning)" : undefined,
        }}
      >
        {value}
      </dd>
    </div>
  );
}
