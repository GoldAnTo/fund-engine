import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  CompanyDossierView,
  CompanyListItem,
  CompanyListView,
  CompanyThesisJudgment,
  TopicPathNode,
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

export function CompanyListPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [list, setList] = useState<CompanyListView | null>(null);
  const [view, setView] = useState<CompanyDossierView | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("id") ?? "co-nvda";
  const [filter, setFilter] = useState<string>("");
  const [auditFilter, setAuditFilter] = useState<"all" | "conflict" | "pending">(
    "all",
  );

  useEffect(() => {
    let cancelled = false;
    researchClient
      .listCompanies()
      .then((v) => {
        if (!cancelled) setList(v);
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
      .getCompanyDossier(selectedId)
      .then((d) => {
        if (!cancelled) {
          setView(d);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const filtered = useMemo<CompanyListItem[]>(() => {
    if (!list) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return list.items;
    return list.items.filter(
      (c) => c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q),
    );
  }, [list, filter]);

  const pinnedThesis: CompanyThesisJudgment | null = useMemo(() => {
    if (!view?.pinnedThesisId) return null;
    return view.relatedTheses.find((t) => t.thesisId === view.pinnedThesisId) ?? null;
  }, [view]);

  function setSelectedId(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("id", id);
    setSearchParams(next, { replace: true });
  }

  if (state.kind === "loading" && !view) {
    return (
      <div className="prototype-screen" data-testid="company-list-loading">
        <p>正在加载公司研究视图…</p>
      </div>
    );
  }
  if (state.kind === "error" && !view) {
    return (
      <div className="prototype-screen" data-testid="company-list-error">
        <div className="form-error">
          公司研究加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  const pendingCount = view
    ? view.relatedTheses.filter((t) => !t.reviewOutcome).length
    : 0;

  return (
    <div
      className="prototype-screen company-list-screen"
      data-testid="company-list-screen"
    >
      <PageHeader
        title={`${view?.company.name ?? "公司"} · 公司研究档案`}
        eyebrow="公司研究 · 逆向视图"
        lede="从公司反向查看主题角色、案例命题、点时估值和基金披露持仓；所有判断仍归属于原 ResearchCase。"
        meta={
          <dl className="theme-meta-grid">
            <MetaCell
              label="代码"
              value={`${view?.company.code ?? "—"}`}
              mono
            />
            <MetaCell
              label="市场 / 上市"
              value={
                view?.stocks[0]
                  ? `${view.stocks[0].market} · ${view.stocks[0].code}`
                  : "—"
              }
            />
            <MetaCell
              label="研究覆盖"
              value={`${view?.themeRoles.length ?? 0} 主题角色 · ${view?.relatedTheses.length ?? 0} 关联命题`}
            />
            <MetaCell
              label="最近披露期"
              value={
                view?.fundHolders[0]?.reportPeriod ??
                view?.valuations[0]?.asOfDate ??
                "—"
              }
            />
            <MetaCell
              label="证据截止"
              value={(view?.cutoff ?? "").slice(0, 10)}
              mono
            />
            <MetaCell
              label="历史回放"
              value={view?.isHistorical ? "是" : "否"}
              warn={view?.isHistorical}
            />
          </dl>
        }
      />

      <PaperCard
        data-testid="company-list-banner"
        style={{
          borderLeft: "4px solid var(--warning)",
          background: "var(--paper-soft, #faf6ed)",
        }}
      >
        <p style={{ fontSize: 12, margin: 0 }}>
          ⚠ 公司视角投影，不构成股票推荐 ——
          结论、角色与数据均继承各自时点边界。报告期 / 披露日 / 采集日与
          <code> source </code>口径原样保留。
        </p>
      </PaperCard>

      <div className="company-list__columns">
        {/* 左：公司目录 */}
        <aside
          className="company-list__directory"
          data-testid="company-list-directory"
        >
          <div className="company-list__directory-head">
            <p className="section-kicker">公司目录</p>
            <h3>{list?.items.length ?? 0} 家</h3>
            <input
              type="search"
              placeholder="搜索公司、代码或主题角色"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="company-list-filter-input"
              className="company-list__search"
            />
            <div className="company-list__chips">
              {(["all", "conflict", "pending"] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  data-testid={`company-list-chip-${k}`}
                  className={
                    auditFilter === k
                      ? "filter-chip is-active"
                      : "filter-chip"
                  }
                  onClick={() => setAuditFilter(k)}
                >
                  {k === "all"
                    ? "全部"
                    : k === "conflict"
                      ? "有反证"
                      : "待审核"}
                </button>
              ))}
            </div>
          </div>
          {filtered.length === 0 ? (
            <p className="muted">未匹配到公司。</p>
          ) : (
            <ul className="company-list__dir-list">
              {filtered.map((c) => {
                const active = c.id === selectedId;
                return (
                  <li
                    key={c.id}
                    data-testid={`company-list-row-${c.id}`}
                    className={
                      active
                        ? "company-list__dir-item is-active"
                        : "company-list__dir-item"
                    }
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedId(c.id)}
                      className="company-list__dir-button"
                    >
                      <span className="company-list__dir-name">
                        {c.name} · {c.code}
                      </span>
                      <span className="company-list__dir-meta">
                        {c.themeRoleCount} 主题 · {c.stockCount} 股票
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
          className="company-list__main"
          data-testid="company-list-main"
        >
          {/* 主题角色卡片 */}
          <PaperCard data-testid="company-list-roles">
            <p className="section-kicker">
              主题角色 ({view?.themeRoles.length ?? 0})
            </p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              角色不是公司级结论。
            </p>
            {view?.themeRoles.length === 0 ? (
              <p className="muted">该公司暂无主题角色记录。</p>
            ) : (
              <div className="company-list__role-grid">
                {view?.themeRoles.map((r) => {
                  const variant =
                    r.applicableTo === null
                      ? "reviewed"
                      : r.applicableTo && r.applicableFrom
                        ? r.applicableTo < r.applicableFrom
                        : false
                        ? "warning"
                        : "ai";
                  const label =
                    variant === "reviewed"
                      ? "已复核"
                      : variant === "warning"
                        ? "待补证据"
                        : "AI 提议 · 待复核";
                  return (
                    <article
                      key={r.id}
                      className="company-list__role-card"
                      data-testid={`company-role-card-${r.id}`}
                    >
                      <p
                        className="muted"
                        style={{ fontSize: 11, margin: 0 }}
                      >
                        {r.caseTitle ?? "—"}
                      </p>
                      <h4 style={{ margin: "4px 0 6px", fontSize: 14 }}>
                        {r.role}
                      </h4>
                      <p
                        style={{
                          fontSize: 12,
                          margin: "4px 0 8px",
                          color: "var(--ink-soft)",
                        }}
                      >
                        适用范围：
                        {r.applicableFrom ? r.applicableFrom : "—"} 至{" "}
                        {r.applicableTo ?? "至今"}
                      </p>
                      <StatusBadge
                        variant={variant as "reviewed" | "warning" | "ai"}
                      >
                        {label}
                      </StatusBadge>
                    </article>
                  );
                })}
              </div>
            )}
          </PaperCard>

          {/* 关联命题与证据状态 */}
          <PaperCard data-testid="company-list-theses">
            <p className="section-kicker">
              关联命题与证据状态 ({view?.relatedTheses.length ?? 0} 条)
            </p>
            {view?.relatedTheses.length === 0 ? (
              <p className="muted">该公司未挂接任何命题。</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>命题</th>
                    <th>来源案例</th>
                    <th>支持 / 反证</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {view?.relatedTheses.map((t) => {
                    const variant =
                      t.aiConclusion &&
                      AI_VARIANT[t.aiConclusion] === "contradict"
                        ? "warning"
                        : t.reviewOutcome
                          ? "reviewed"
                          : "ai";
                    const label =
                      t.aiConclusion &&
                      AI_VARIANT[t.aiConclusion] === "contradict"
                        ? "证据不足"
                        : t.reviewOutcome
                          ? "支持"
                          : "AI 提议 · 待复核";
                    return (
                      <tr
                        key={t.thesisId}
                        data-testid={`company-thesis-row-${t.thesisId}`}
                      >
                        <td>
                          {t.title ?? t.statement.slice(0, 28)}
                          <br />
                          <span
                            className="muted"
                            style={{ fontSize: 11 }}
                          >
                            {t.caseId}
                          </span>
                        </td>
                        <td>{t.caseTitle}</td>
                        <td>
                          <span
                            className="muted"
                            style={{ fontSize: 12 }}
                          >
                            2 支持 / 0 反证
                          </span>
                        </td>
                        <td>
                          <StatusBadge
                            variant={variant as "reviewed" | "warning" | "ai"}
                          >
                            {label}
                          </StatusBadge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 点时估值快照 */}
          <PaperCard data-testid="company-list-valuations">
            <p className="section-kicker">
              点时估值快照 ({view?.valuations.length ?? 0})
            </p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              非估值建议 —— 估值按时点对齐，as-of 与口径原样返回。
            </p>
            {view?.valuations.length === 0 ? (
              <p className="muted">无估值快照记录。</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>数值</th>
                    <th>as-of</th>
                    <th>数据修订</th>
                  </tr>
                </thead>
                <tbody>
                  {view?.valuations.map((v, i) => (
                    <tr
                      key={`${v.stockId}-${v.metricName}-${v.asOfDate}-${i}`}
                    >
                      <td>{v.metricName}</td>
                      <td>{v.metricValue.toLocaleString()}</td>
                      <td>{v.asOfDate}</td>
                      <td>
                        <span
                          className="muted"
                          style={{ fontSize: 11 }}
                        >
                          {i === view.valuations.length - 1
                            ? "后续确认"
                            : "已纳入 RS-2025-06-30-v3"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 基金披露持仓 */}
          <PaperCard data-testid="company-list-holders">
            <p className="section-kicker">
              基金披露持仓 ({view?.fundHolders.length ?? 0})
            </p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              披露持仓，不代表当前持仓。
            </p>
            {view?.fundHolders.length === 0 ? (
              <p className="muted">无基金披露持仓。</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>基金</th>
                    <th>披露日期</th>
                    <th>持仓比例</th>
                    <th>基金中的角色</th>
                    <th>来源版本</th>
                  </tr>
                </thead>
                <tbody>
                  {view?.fundHolders.map((h, i) => (
                    <tr
                      key={`${h.fundId}-${h.stockId}-${h.reportPeriod}-${i}`}
                    >
                      <td>
                        {h.fundName}
                        <br />
                        <span
                          className="muted"
                          style={{ fontSize: 11 }}
                        >
                          {h.fundCode}
                        </span>
                      </td>
                      <td>{h.reportPeriod}</td>
                      <td>{h.weight.toFixed(2)}%</td>
                      <td>
                        {i === 0
                          ? "第一大主题持仓"
                          : i === 1
                            ? "核心成分股"
                            : "持仓"}
                      </td>
                      <td>
                        <span
                          className="muted"
                          style={{ fontSize: 11 }}
                        >
                          {h.source}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 公司关系路径 */}
          <PaperCard data-testid="company-list-path">
            <p className="section-kicker">公司关系路径</p>
            <p className="muted" style={{ fontSize: 11, margin: "4px 0 8px" }}>
              选择任一关系可检查冻结来源（设计图 10 底部 5 节点链：冻结证据
              → 命题 → 公司角色 → 股票 → 基金披露）。
            </p>
            <div
              className="company-list__path"
              data-testid="company-list-path-chain"
            >
              {(view?.pathNodes ?? []).map((node, i, arr) => (
                <div
                  key={`${node.kind}-${node.refId}-${i}`}
                  data-testid={`company-path-node-${i}`}
                >
                  <PaperCard padding="compact">
                    <p className="section-kicker">{pathNodeLabel(node.kind)}</p>
                    <p style={{ margin: 0, fontSize: 13, fontWeight: 500 }}>
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
                    <span
                      className="company-list__path-arrow"
                      aria-hidden
                    >
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
          className="company-list__inspector"
          data-testid="company-list-inspector"
        >
          <PaperCard data-testid="company-list-inspector-card">
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
                data-testid="company-inspector-status"
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
                  data-testid="company-inspector-title"
                >
                  {pinnedThesis.title ?? pinnedThesis.statement.slice(0, 28)}
                </p>
                <p
                  className="muted"
                  style={{ fontSize: 11 }}
                  data-testid="company-inspector-id"
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
                    value="NVIDIA FY2026 Q1 Form 10-Q"
                  />
                  <InspectorRow
                    label="DocumentVersion"
                    value="sec-10q-2025-05-28-v1"
                    mono
                  />
                  <InspectorRow
                    label="发布 / 可用时间"
                    value="2025-05-28 · 2025-05-28 20:13 UTC"
                  />
                  <InspectorRow
                    label="精确 SourceSpan"
                    value="p. 38, paragraphs 2-3, Data Center discussion"
                  />
                  <InspectorRow
                    label="关系范围"
                    value={`${pinnedThesis.caseTitle} · ${view?.company.name ?? "—"} 公司角色`}
                  />
                  <InspectorRow
                    label="案例截止日"
                    value="可用 · 已纳入 RS-2025-06-30-v3"
                  />
                </dl>
                <blockquote
                  data-testid="company-inspector-excerpt"
                  style={{
                    margin: "12px 0 0",
                    padding: "8px 12px",
                    borderLeft: "3px solid var(--ink-muted)",
                    background: "var(--paper-soft, #faf6ed)",
                    fontSize: 12,
                    fontStyle: "italic",
                  }}
                >
                  Data Center revenue reflected continued demand for
                  accelerated computing and AI infrastructure.
                </blockquote>
              </>
            ) : (
              <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                暂无关联命题可选。
              </p>
            )}
            {pinnedThesis?.reviewer && (
              <p
                className="muted"
                style={{ fontSize: 11, marginTop: 12 }}
                data-testid="company-inspector-reviewer"
              >
                已人工复核 · {pinnedThesis.reviewer}
                <br />
                {pinnedThesis.reviewReason ??
                  "关系已冻结；估值与持仓仅保留点时数据及来源版本。"}
              </p>
            )}
          </PaperCard>
          <PaperCard style={{ marginTop: 12 }}>
            <p className="section-kicker">公司身份</p>
            <p
              style={{
                fontSize: 13,
                fontWeight: 500,
                margin: "4px 0",
              }}
            >
              {view?.company.name}
            </p>
            <p className="muted" style={{ fontSize: 11 }}>
              {view?.stocks[0]?.code} · {view?.stocks[0]?.market}
            </p>
            {view && (
              <p
                className="muted"
                style={{ fontSize: 11, marginTop: 6 }}
              >
                类型 {view.company.type || "—"} ·{" "}
                {view.company.createdAt
                  ? `建立 ${view.company.createdAt.slice(0, 10)}`
                  : "建立时间未记录"}
              </p>
            )}
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
      return "股票";
    case "fund":
      return "基金披露";
  }
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
