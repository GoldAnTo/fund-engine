import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  CompanyListItem,
  TopicListItem,
  TopicPathNode,
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
  confirmed: "已复核",
  modified: "已修正",
  rejected: "已驳回",
};

export function TopicListPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [items, setItems] = useState<TopicListItem[]>([]);
  const [view, setView] = useState<TopicView | null>(null);
  const [companies, setCompanies] = useState<CompanyListItem[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedTag = searchParams.get("tag") ?? "AI 算力基础设施";
  const [filter, setFilter] = useState<string>("");
  const [auditFilter, setAuditFilter] = useState<"all" | "confirmed" | "pending">(
    "all",
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      researchClient.listThemes().catch(() => [] as TopicListItem[]),
      researchClient.listCompanies().catch(
        () => ({ items: [] as CompanyListItem[], hasMore: false, nextCursor: null }),
      ),
    ])
      .then(([topics, comps]) => {
        if (!cancelled) {
          setItems(topics);
          setCompanies(comps.items);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    researchClient
      .getThemeView(selectedTag)
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
  }, [selectedTag]);

  const filtered = useMemo<TopicListItem[]>(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((t) => t.tag.toLowerCase().includes(q));
  }, [items, filter]);

  // 当前主题内被右栏锁定的命题（mock 派生）
  const pinnedThesis: TopicThesisView | null = useMemo(() => {
    if (!view?.pinnedThesisId) return null;
    for (const c of view.cases) {
      const t = c.theses.find((th) => th.thesisId === view.pinnedThesisId);
      if (t) return t;
    }
    return null;
  }, [view]);

  function setSelectedTag(tag: string) {
    const next = new URLSearchParams(searchParams);
    next.set("tag", tag);
    setSearchParams(next, { replace: true });
  }

  const totalThesisCount = view?.derivedFrom.thesisIds.length ?? 0;
  const pendingReviewCount = view
    ? view.cases.flatMap((c) => c.theses).filter((t) => !t.reviewOutcome).length
    : 0;

  if (state.kind === "loading" && !view) {
    return (
      <div className="prototype-screen" data-testid="topic-list-loading">
        <p>正在加载主题研究视图…</p>
      </div>
    );
  }
  if (state.kind === "error" && !view) {
    return (
      <div className="prototype-screen" data-testid="topic-list-error">
        <div className="form-error">
          主题研究加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div
      className="prototype-screen topic-list-screen"
      data-testid="topic-list-screen"
    >
      <PageHeader
        title={view?.tag ?? selectedTag}
        eyebrow="主题研究 · 横切主题聚合"
        lede="从多个 ResearchCase 聚合命题、公司角色和披露持仓；每一项都可回链到冻结证据与人工审核记录。"
        meta={
          <dl className="theme-meta-grid">
            <MetaCell label="证据截止" value={(view?.cutoff ?? "").slice(0, 10)} />
            <MetaCell
              label="冻结快照"
              value="RS-2025-06-30-v3"
              mono
            />
            <MetaCell
              label="待复核关系"
              value={`${pendingReviewCount} 条`}
              warn={pendingReviewCount > 0}
            />
            <MetaCell label="聚合案例" value={`${view?.cases.length ?? 0}`} />
            <MetaCell label="聚合命题" value={`${totalThesisCount}`} />
          </dl>
        }
      />

      <PaperCard
        data-testid="topic-list-banner"
        style={{
          borderLeft: "4px solid var(--warning)",
          background: "var(--paper-soft, #faf6ed)",
        }}
      >
        <p style={{ fontSize: 12, margin: 0 }}>
          ⚠ 聚合投影，不构成主题级统一结论 ——
          所有状态继承案例层原审核结果。每个聚合数字均可展开到{" "}
          <code>derived_from</code> 明细并继续下钻到案例层 AIAssessment /
          ReviewDecision / SourceStatement。
        </p>
      </PaperCard>

      <div className="topic-list__columns">
        {/* 左：主题目录 */}
        <aside
          className="topic-list__directory"
          data-testid="topic-list-directory"
        >
          <div className="topic-list__directory-head">
            <p className="section-kicker">主题目录</p>
            <h3>{items.length} 个主题</h3>
            <input
              type="search"
              placeholder="搜索主题、公司或命题"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="topic-list-filter-input"
              className="topic-list__search"
            />
            <div className="topic-list__chips">
              {(["all", "confirmed", "pending"] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  data-testid={`topic-list-chip-${k}`}
                  className={
                    auditFilter === k
                      ? "filter-chip is-active"
                      : "filter-chip"
                  }
                  onClick={() => setAuditFilter(k)}
                >
                  {k === "all"
                    ? "全部"
                    : k === "confirmed"
                      ? "有变化"
                      : "待复核"}
                </button>
              ))}
            </div>
          </div>
          {filtered.length === 0 ? (
            <p className="muted">未匹配到主题。</p>
          ) : (
            <ul className="topic-list__dir-list">
              {filtered.map((t) => {
                const active = t.tag === selectedTag;
                return (
                  <li
                    key={t.tag}
                    data-testid={`topic-list-row-${encodeURIComponent(t.tag)}`}
                    className={
                      active
                        ? "topic-list__dir-item is-active"
                        : "topic-list__dir-item"
                    }
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedTag(t.tag)}
                      className="topic-list__dir-button"
                    >
                      <span className="topic-list__dir-name">{t.tag}</span>
                      <span className="topic-list__dir-meta">
                        {t.caseCount} 案例 · {t.companyCount} 公司 ·{" "}
                        {t.thesisCount} 命题
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        {/* 中：主区内容 */}
        <section
          className="topic-list__main"
          data-testid="topic-list-main"
        >
          {/* ResearchCase 与命题 */}
          <PaperCard data-testid="topic-list-cases">
            <p className="section-kicker">
              ResearchCase 与命题 ({view?.cases.length ?? 0} 案例)
            </p>
            {view?.cases.length === 0 ? (
              <p className="muted">该主题暂无参与案例。</p>
            ) : (
              <div className="topic-list__case-grid">
                {view?.cases.map((c) => (
                  <CaseCard
                    key={c.caseId}
                    caseId={c.caseId}
                    caseTitle={c.caseTitle}
                    thesisCounts={c.thesisCounts}
                    theses={c.theses.slice(0, 3)}
                    pinnedId={view.pinnedThesisId}
                  />
                ))}
              </div>
            )}
          </PaperCard>

          {/* 公司 × 主题角色 */}
          <PaperCard data-testid="topic-list-roles">
            <p className="section-kicker">
              公司 × 主题角色 ({view?.companyRoles.length ?? 0})
            </p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              角色为点时关系，不是公司级结论。
            </p>
            {view?.companyRoles.length === 0 ? (
              <p className="muted">无公司角色记录。</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>公司</th>
                    <th>主题角色</th>
                    <th>关联命题</th>
                    <th>证据状态</th>
                    <th>适用范围</th>
                  </tr>
                </thead>
                <tbody>
                  {view?.companyRoles.map((r, i) => {
                    const status =
                      i === 0
                        ? "已复核支持"
                        : i === 1
                          ? "待补证据"
                          : "AI 提议 · 待复核";
                    const variant =
                      status === "已复核支持"
                        ? "reviewed"
                        : status === "待补证据"
                          ? "warning"
                          : "ai";
                    const range = r.applicableFrom
                      ? `${r.applicableFrom} · 当前主题聚合`
                      : "—";
                    return (
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
                        </td>
                        <td>{r.role}</td>
                        <td className="muted" style={{ fontSize: 12 }}>
                          {r.caseTitle ?? "—"}
                        </td>
                        <td>
                          <StatusBadge variant={variant as "reviewed" | "warning" | "ai"}>
                            {status}
                          </StatusBadge>
                        </td>
                        <td>
                          <span className="muted" style={{ fontSize: 12 }}>
                            {range}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 披露基金暴露 */}
          <PaperCard data-testid="topic-list-exposure">
            <p className="section-kicker">
              披露基金暴露 ({view?.fundExposure.length ?? 0})
            </p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              披露持仓，不代表当前持仓或推荐。
            </p>
            {view?.fundExposure.length === 0 ? (
              <p className="muted">无基金披露持仓。</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>基金</th>
                    <th>披露日期</th>
                    <th>主题暴露</th>
                    <th>主要映射</th>
                    <th>数据状态</th>
                  </tr>
                </thead>
                <tbody>
                  {view?.fundExposure.map((p, i) => (
                    <tr
                      key={`${p.fundId}-${p.stockId}-${p.reportPeriod}-${i}`}
                    >
                      <td>
                        <Link
                          to={`/companies/${encodeURIComponent(p.stockId)}`}
                          className="prototype-link"
                        >
                          {p.fundName}
                        </Link>
                      </td>
                      <td>{p.reportPeriod}</td>
                      <td>{p.weight.toFixed(1)}%</td>
                      <td>
                        {p.stockName}
                        <br />
                        <span className="muted" style={{ fontSize: 11 }}>
                          {p.stockCode}
                        </span>
                      </td>
                      <td>
                        <span className="muted" style={{ fontSize: 11 }}>
                          {i === 0 ? "案例截止日可用" : "已冻结披露"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 主题关系路径 */}
          <PaperCard data-testid="topic-list-path">
            <p className="section-kicker">主题关系路径</p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              选择任一节点可检查来源（设计图 9 底部 5 节点链：冻结证据 → 命题
              → 公司角色 → 股票映射 → 基金披露）。
            </p>
            <div className="topic-list__path" data-testid="topic-list-path-chain">
              {(view?.pathNodes ?? []).map((node, i, arr) => (
                <div
                  key={`${node.kind}-${node.refId}-${i}`}
                  data-testid={`topic-path-node-${i}`}
                >
                  <PaperCard padding="compact">
                    <p className="section-kicker">{pathNodeLabel(node.kind)}</p>
                    <p
                      style={{
                        margin: 0,
                        fontSize: 13,
                        fontWeight: 500,
                      }}
                    >
                      {node.label}
                    </p>
                    <p
                      className="muted"
                      style={{ fontSize: 11, margin: "4px 0 0" }}
                    >
                      {node.meta}
                    </p>
                  </PaperCard>
                  {i < arr.length - 1 && (
                    <span className="topic-list__path-arrow" aria-hidden>
                      →
                    </span>
                  )}
                </div>
              ))}
            </div>
          </PaperCard>
        </section>

        {/* 右：固定证据检查器 */}
        <aside
          className="topic-list__inspector"
          data-testid="topic-list-inspector"
        >
          <PaperCard data-testid="topic-list-inspector-card">
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <p className="section-kicker">固定证据检查器</p>
              <span
                className="state-badge reviewed"
                data-testid="topic-inspector-status"
              >
                {pinnedThesis?.reviewOutcome
                  ? REVIEW_LABEL[pinnedThesis.reviewOutcome] ?? "已人工复核"
                  : "AI 草案 · 待复核"}
              </span>
            </div>
            {pinnedThesis ? (
              <>
                <p
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    margin: "8px 0 4px",
                  }}
                  data-testid="topic-inspector-title"
                >
                  {pinnedThesis.title ?? pinnedThesis.statement.slice(0, 28)}
                </p>
                <p
                  className="muted"
                  style={{ fontSize: 11 }}
                  data-testid="topic-inspector-id"
                >
                  当前选中 · {pinnedThesis.thesisId}
                </p>
                <dl
                  style={{
                    marginTop: 12,
                    display: "grid",
                    gridTemplateColumns: "auto 1fr",
                    gap: "6px 12px",
                    fontSize: 12,
                  }}
                >
                  <InspectorRow
                    label="来源名称"
                    value="Microsoft FY2025 Q3 earnings call transcript"
                  />
                  <InspectorRow
                    label="DocumentVersion"
                    value="issuer-call-2025-04-30-v1"
                    mono
                  />
                  <InspectorRow
                    label="发布 / 可用时间"
                    value="2025-04-30 · 2025-04-30 21:44 UTC"
                  />
                  <InspectorRow
                    label="精确 SourceSpan"
                    value="prepared remarks, pp. 4-5, capacity constraints and revenue timing"
                  />
                  <InspectorRow
                    label="适用范围"
                    value={`AI 算力基础设施 · 当前主题聚合`}
                  />
                </dl>
                <blockquote
                  data-testid="topic-inspector-excerpt"
                  style={{
                    margin: "12px 0 0",
                    padding: "8px 12px",
                    borderLeft: "3px solid var(--ink-muted)",
                    background: "var(--paper-soft, #faf6ed)",
                    fontSize: 12,
                    fontStyle: "italic",
                  }}
                >
                  "Demand for our AI services remained higher than our
                  available capacity."
                </blockquote>
              </>
            ) : (
              <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                暂无命题可选。
              </p>
            )}
            {pinnedThesis?.reviewer && (
              <p
                className="muted"
                style={{ fontSize: 11, marginTop: 12 }}
                data-testid="topic-inspector-reviewer"
              >
                已人工复核 · {pinnedThesis.reviewer}
                <br />
                {pinnedThesis.reviewReason ??
                  "关系、角色和适用范围已纳入冻结快照；后续更正如追加新版本。"}
              </p>
            )}
          </PaperCard>
          {/* 公司清单侧边摘要（mock 数据用） */}
          <PaperCard style={{ marginTop: 12 }}>
            <p className="section-kicker">参与公司（mock）</p>
            <ul style={{ paddingLeft: 16, fontSize: 12 }}>
              {companies.slice(0, 4).map((c) => (
                <li key={c.id}>
                  {c.name} · {c.code}
                </li>
              ))}
            </ul>
          </PaperCard>
        </aside>
      </div>
    </div>
  );
}

function pathNodeLabel(kind: TopicPathNode["kind"]): string {
  switch (kind) {
    case "evidence":
      return "冻结证据";
    case "thesis":
      return "命题";
    case "role":
      return "公司角色";
    case "stock":
      return "股票映射";
    case "fund":
      return "基金披露";
  }
}

function CaseCard({
  caseId,
  caseTitle,
  thesisCounts,
  theses,
  pinnedId,
}: {
  caseId: string;
  caseTitle: string;
  thesisCounts: Record<string, number>;
  theses: TopicThesisView[];
  pinnedId: string | null | undefined;
}) {
  const primary = theses[0];
  const counts = Object.entries(thesisCounts).filter(([, n]) => n > 0);
  return (
    <article
      className="topic-list__case-card"
      data-testid={`topic-case-card-${caseId}`}
    >
      <header>
        <p className="muted" style={{ fontSize: 11, margin: 0 }}>
          {caseId}
        </p>
        <h4 style={{ margin: "4px 0 6px", fontSize: 14 }}>{caseTitle}</h4>
      </header>
      {primary && (
        <>
          <p style={{ fontSize: 12, margin: "4px 0 8px", color: "var(--ink-soft)" }}>
            {primary.statement}
          </p>
          <div
            style={{
              display: "flex",
              gap: 4,
              flexWrap: "wrap",
              marginBottom: 8,
            }}
          >
            {counts.map(([k, n]) => (
              <span
                key={k}
                className={`status-pill status-pill--${
                  k === "supported"
                    ? "support"
                    : k === "contradicted"
                      ? "contradict"
                      : k === "insufficient_evidence"
                        ? "warning"
                        : k === "ai_pending"
                          ? "ai"
                          : "draft"
                }`}
                data-testid={`topic-case-count-${caseId}-${k}`}
              >
                {k} {n}
              </span>
            ))}
          </div>
          {primary.aiConclusion && (
            <div
              style={{
                display: "flex",
                gap: 6,
                alignItems: "center",
                flexWrap: "wrap",
                fontSize: 11,
              }}
              data-testid={
                primary.thesisId === pinnedId
                  ? `topic-case-pinned-${caseId}`
                  : undefined
              }
            >
              <StatusBadge
                variant={AI_VARIANT[primary.aiConclusion] ?? "ai"}
              >
                AI · {AI_LABEL[primary.aiConclusion] ?? "未评估"}
                {primary.aiProvisional ? " · 草案" : ""}
              </StatusBadge>
              {primary.reviewOutcome ? (
                <StatusBadge
                  variant={REVIEW_VARIANT[primary.reviewOutcome] ?? "reviewed"}
                >
                  人工 · {REVIEW_LABEL[primary.reviewOutcome] ?? primary.reviewOutcome}
                </StatusBadge>
              ) : (
                <StatusBadge variant="ai">人工 · 待复核</StatusBadge>
              )}
            </div>
          )}
        </>
      )}
    </article>
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

function InspectorRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="muted" style={{ fontSize: 11 }}>
        {label}
      </dt>
      <dd
        style={{
          fontSize: 12,
          fontFamily: mono ? "var(--font-mono, monospace)" : undefined,
          wordBreak: "break-word",
        }}
      >
        {value}
      </dd>
    </>
  );
}
