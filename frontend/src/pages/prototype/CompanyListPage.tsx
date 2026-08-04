import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PaperCard } from "../../components/prototype/PaperCard";
import type {
  CompanyDossierView,
  CompanyFundHolderView,
  CompanyListItem,
  CompanyListView,
  CompanyThemeRoleView,
  CompanyThesisJudgment,
  CompanyValuationView,
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
  const requestedId = searchParams.get("id");
  const selectedId =
    (requestedId && list?.items.some((company) => company.id === requestedId)
      ? requestedId
      : list?.items[0]?.id) ?? "";
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
    if (!selectedId) return;
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

  return (
    <div
      className="prototype-screen company-list-screen"
      data-testid="company-list-screen"
    >
      {/* 顶部 banner：横跨三栏，左=NVIDIA 标题+lede，右=3 个 meta 卡片（设计图 10） */}
      <header
        className="company-list__banner"
        data-testid="company-list-banner"
      >
        <div className="company-list__banner-main">
          <p className="eyebrow">COMPANY-CENTRIC RESEARCH DOSSIER</p>
          <h1>{view?.company.name ?? "公司"} · 公司研究档案</h1>
          <p className="lede">
            从公司反向查看主题角色、案例命题、点时估值和基金披露持仓；
            所有判断仍归属于原 ResearchCase。
          </p>
        </div>
        <dl className="company-list__banner-meta">
          <BannerMeta label="证据截止" value="2025-06-30" />
          <BannerMeta label="冻结快照" value="RS-2025-06-30-v3" mono />
          <BannerMeta
            label="待复核关系"
            value={`${
              view?.relatedTheses.filter((t) => !t.reviewOutcome).length ?? 0
            } 条`}
            warn={
              (view?.relatedTheses.filter((t) => !t.reviewOutcome).length ??
                0) > 0
            }
          />
        </dl>
      </header>

      {/* 警告条 */}
      <PaperCard
        style={{
          borderLeft: "4px solid var(--warning)",
          background: "var(--paper-soft, #faf6ed)",
        }}
        data-testid="company-list-warning"
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 16,
          }}
        >
          <p
            style={{
              fontSize: 12,
              fontWeight: 500,
              margin: 0,
              color: "var(--ink-strong, #1c1b18)",
            }}
          >
            公司视角投影，不构成股票推荐
          </p>
          <p
            className="muted"
            style={{ fontSize: 12, margin: 0, textAlign: "right" }}
          >
            结论、角色与数据均继承各自时点边界
          </p>
        </div>
      </PaperCard>

      <div className="company-list__columns">
        {/* 左：公司目录（设计图 10 左 280，48 家） */}
        <aside
          className="company-list__directory"
          data-testid="company-list-directory"
        >
          <div className="company-list__directory-head">
            <p
              style={{
                fontSize: 13,
                fontWeight: 500,
                margin: 0,
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span>公司目录</span>
              <span className="muted">{list?.items.length ?? 0} 家</span>
            </p>
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
                      <div className="company-list__dir-row">
                        <span className="company-list__dir-name">
                          {c.name} · {c.code}
                        </span>
                        {active && (
                          <span
                            className="muted"
                            style={{ fontSize: 11 }}
                          >
                            当前
                          </span>
                        )}
                      </div>
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

        {/* 中：主区（设计图 10 中部：3 张公司身份卡 + 3 张主题角色卡 + 4 个段） */}
        <section
          className="company-list__main"
          data-testid="company-list-main"
        >
          {/* 3 张公司身份卡（横排）—— 设计图 10 中部上方 */}
          <div
            className="company-list__id-grid"
            data-testid="company-list-id-cards"
          >
            <IdCard
              kicker="公司身份"
              title={view?.company.name ?? "—"}
              meta={
                view?.company.market
                  ? `${view.company.code} · ${view.company.market} · ${view.company.listedLabel ?? "已上市"}`
                  : view?.company.code ?? "—"
              }
            />
            <IdCard
              kicker="研究覆盖"
              title={
                view?.themeRoles
                  .map((r) => r.caseTitle)
                  .filter((v, i, a) => v && a.indexOf(v) === i)
                  .slice(0, 3)
                  .join(" / ") ?? "—"
              }
              meta={
                view
                  ? `${view.themeRoles.length} 主题角色 · ${view.relatedTheses.length} 关联命题`
                  : "—"
              }
            />
            <IdCard
              kicker="最近披露期"
              title={view?.company.reportPeriod ?? "—"}
              meta={view?.company.reportNote ?? "—"}
            />
          </div>

          {/* 3 张主题角色卡（横排） */}
          <PaperCard data-testid="company-list-roles">
            <div className="company-list__section-head">
              <p className="section-kicker">
                主题角色 ({view?.themeRoles.length ?? 0})
              </p>
              <p className="muted" style={{ fontSize: 11 }}>
                角色不是公司级结论。
              </p>
            </div>
            {view?.themeRoles.length === 0 ? (
              <p className="muted">该公司暂无主题角色记录。</p>
            ) : (
              <div className="company-list__role-grid">
                {view?.themeRoles.map((r) => (
                  <RoleCard key={r.id} role={r} />
                ))}
              </div>
            )}
          </PaperCard>

          {/* 关联命题与证据状态 表 */}
          <PaperCard data-testid="company-list-theses">
            <div className="company-list__section-head">
              <p className="section-kicker">
                关联命题与证据状态 ({view?.relatedTheses.length ?? 0} 条)
              </p>
            </div>
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
                  {view?.relatedTheses.map((t) => (
                    <ThesisRow key={t.thesisId} thesis={t} />
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 点时估值快照 表 */}
          <PaperCard data-testid="company-list-valuations">
            <div className="company-list__section-head">
              <p className="section-kicker">
                点时估值快照 ({view?.valuations.length ?? 0})
              </p>
              <p
                className="muted"
                style={{ fontSize: 11 }}
              >
                非估值建议 —— 估值按时点对齐，as-of 与口径原样返回。
              </p>
            </div>
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
                    <ValuationRow
                      key={`${v.stockId}-${v.metricName}-${v.asOfDate}-${i}`}
                      valuation={v}
                      isLast={i === view.valuations.length - 1}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 基金披露持仓 表 */}
          <PaperCard data-testid="company-list-holders">
            <div className="company-list__section-head">
              <p className="section-kicker">
                基金披露持仓 ({view?.fundHolders.length ?? 0})
              </p>
              <p
                className="muted"
                style={{ fontSize: 11 }}
              >
                披露持仓，不代表当前持仓。
              </p>
            </div>
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
                    <HolderRow
                      key={`${h.fundId}-${h.stockId}-${h.reportPeriod}-${i}`}
                      holder={h}
                      isFirst={i === 0}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 公司关系路径 5 节点链 */}
          <PaperCard data-testid="company-list-path">
            <div className="company-list__section-head">
              <p className="section-kicker">公司关系路径</p>
              <p className="muted" style={{ fontSize: 11 }}>
                选择任一关系可检查冻结来源（5 节点链：冻结证据 → 命题 →
                公司角色 → 股票 → 基金披露）。
              </p>
            </div>
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
                    <span className="company-list__path-arrow" aria-hidden>
                      →
                    </span>
                  )}
                </div>
              ))}
            </div>
          </PaperCard>
        </section>

        {/* 右：固定证据检查器（设计图 10 右侧 320） */}
        <aside
          className="company-list__inspector"
          data-testid="company-list-inspector"
        >
          <PaperCard data-testid="company-list-inspector-card">
            <div className="company-list__inspector-head">
              <p className="eyebrow" style={{ margin: 0 }}>
                固定证据检查器
              </p>
              <span
                className="state-badge reviewed"
                data-testid="company-inspector-status"
              >
                {pinnedThesis?.reviewOutcome
                  ? REVIEW_LABEL[pinnedThesis.reviewOutcome] ?? "已复核"
                  : "已复核支持"}
              </span>
            </div>
            {pinnedThesis ? (
              <>
                <p
                  style={{
                    fontSize: 11,
                    margin: "12px 0 4px",
                    color: "var(--ink-muted)",
                  }}
                  data-testid="company-inspector-id"
                >
                  当前选中 · {pinnedThesis.thesisId}
                </p>
                <h3
                  style={{
                    fontSize: 14,
                    fontWeight: 500,
                    margin: "0 0 12px",
                    lineHeight: 1.35,
                  }}
                  data-testid="company-inspector-title"
                >
                  {pinnedThesis.title ?? pinnedThesis.statement.slice(0, 28)}
                </h3>
                <dl
                  style={{
                    margin: 0,
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
                    value={`${pinnedThesis.caseTitle} · ${
                      view?.company.name ?? "—"
                    } 公司角色`}
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
                <span style={{ color: "var(--ink-strong, #1c1b18)" }}>
                  已人工复核 · {pinnedThesis.reviewer}
                </span>
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
              {view?.stocks[0]?.code} · {view?.company.market ?? "—"}
            </p>
            {view?.company.createdAt && (
              <p
                className="muted"
                style={{ fontSize: 11, marginTop: 6 }}
              >
                建立 {view.company.createdAt.slice(0, 10)}
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

function IdCard({
  kicker,
  title,
  meta,
}: {
  kicker: string;
  title: string;
  meta: string;
}) {
  return (
    <article className="company-list__id-card">
      <p
        className="muted"
        style={{ fontSize: 11, margin: 0 }}
      >
        {kicker}
      </p>
      <p
        style={{
          fontSize: 13,
          fontWeight: 500,
          margin: "4px 0 4px",
          lineHeight: 1.35,
        }}
      >
        {title}
      </p>
      <p
        className="muted"
        style={{ fontSize: 11, margin: 0 }}
      >
        {meta}
      </p>
    </article>
  );
}

function RoleCard({ role }: { role: CompanyThemeRoleView }) {
  const variant =
    role.statusVariant ??
    (role.applicableTo === null ? "reviewed" : "ai");
  const label = role.statusLabel ?? "—";
  return (
    <article
      className="company-list__role-card"
      data-testid={`company-role-card-${role.id}`}
    >
      <p
        className="muted"
        style={{ fontSize: 11, margin: 0 }}
      >
        {role.caseTitle ?? "—"}
      </p>
      <h4 style={{ margin: "4px 0 4px", fontSize: 14, lineHeight: 1.3 }}>
        {role.role}
      </h4>
      {role.transmission && (
        <p
          style={{
            fontSize: 12,
            margin: "0 0 8px",
            color: "var(--ink-soft)",
            lineHeight: 1.45,
          }}
        >
          {role.transmission}
        </p>
      )}
      <span
        className={`status-pill status-pill--${variant}`}
        data-testid={`company-role-status-${role.id}`}
      >
        {label}
      </span>
    </article>
  );
}

function ThesisRow({ thesis }: { thesis: CompanyThesisJudgment }) {
  // 设计图 10 的"支持/反证"列根据 aiConclusion + reviewOutcome 估算
  // （mock 不带 link 数，用 ai 结论 + review 结果二选一）
  const supportReject =
    thesis.aiConclusion === "supported" ? "2 支持 / 0 反证" : "1 支持 / 1 反证";
  const statusLabel =
    thesis.reviewOutcome === "modified" && thesis.aiConclusion === "insufficient_evidence"
      ? "证据不足"
      : thesis.reviewOutcome === "confirmed"
        ? "支持"
        : "AI 提议";
  const statusVariant =
    statusLabel === "支持"
      ? "reviewed"
      : statusLabel === "证据不足"
        ? "warning"
        : "ai";
  return (
    <tr data-testid={`company-thesis-row-${thesis.thesisId}`}>
      <td>
        {thesis.title ?? thesis.statement.slice(0, 28)}
        <br />
        <span
          className="muted"
          style={{ fontSize: 11 }}
        >
          {thesis.thesisId}
        </span>
      </td>
      <td>
        {thesis.caseId}
        <br />
        <span className="muted" style={{ fontSize: 11 }}>
          {supportReject}
        </span>
      </td>
      <td>
        <span className="muted" style={{ fontSize: 12 }}>
          {supportReject}
        </span>
      </td>
      <td>
        <span className={`status-pill status-pill--${statusVariant}`}>
          {statusLabel}
        </span>
      </td>
    </tr>
  );
}

function ValuationRow({
  valuation,
  isLast,
}: {
  valuation: CompanyValuationView;
  isLast: boolean;
}) {
  // 设计图 10 的"数据修订"列：根据 metric 字段 + 是否最后一行映射
  // 到"已纳入 RS-2025-06-30-v3" / "已纳入" / "后续确认"
  const revisionLabel = isLast
    ? "后续确认"
    : `已纳入 ${valuation.asOfDate.replace(/-/g, "")}`;
  const variant = isLast ? "warning" : "reviewed";
  return (
    <tr
      data-testid={`company-valuation-row-${valuation.metricName}-${valuation.asOfDate}`}
    >
      <td>{valuation.metricName}</td>
      <td>{valuation.metricValue.toLocaleString()}</td>
      <td>{valuation.asOfDate}</td>
      <td>
        <span
          className={`status-pill status-pill--${variant}`}
          style={{ fontSize: 11 }}
        >
          {revisionLabel}
        </span>
      </td>
    </tr>
  );
}

function HolderRow({
  holder,
  isFirst,
}: {
  holder: CompanyFundHolderView;
  isFirst: boolean;
}) {
  // 设计图 10 的"基金中的角色"列："第一大主题持仓" / "核心成分股" / "持仓"
  const roleLabel = isFirst
    ? "第一大主题持仓"
    : holder.weight > 7
      ? "核心成分股"
      : "持仓";
  return (
    <tr data-testid={`company-holder-row-${holder.fundId}`}>
      <td>
        {holder.fundName}
        <br />
        <span
          className="muted"
          style={{ fontSize: 11 }}
        >
          {holder.fundCode}
        </span>
      </td>
      <td>{holder.reportPeriod}</td>
      <td>{holder.weight.toFixed(2)}%</td>
      <td>{roleLabel}</td>
      <td>
        <span
          className="muted"
          style={{ fontSize: 11 }}
        >
          {holder.source}
        </span>
      </td>
    </tr>
  );
}

function BannerMeta({
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
    <div className="company-list__banner-cell">
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
