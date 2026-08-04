import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PaperCard } from "../../components/prototype/PaperCard";
import type {
  CompanyListItem,
  TopicCaseView,
  TopicCompanyRoleView,
  TopicExposurePosition,
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
  const requestedTag = searchParams.get("tag");
  const selectedTag =
    (requestedTag && items.some((topic) => topic.tag === requestedTag)
      ? requestedTag
      : items[0]?.tag) ?? "";
  const [filter, setFilter] = useState<string>("");
  const [auditFilter, setAuditFilter] = useState<"all" | "confirmed" | "pending">(
    "all",
  );
  const [selectedThesisId, setSelectedThesisId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      researchClient.listThemes().catch(() => [] as TopicListItem[]),
      researchClient
        .listCompanies()
        .catch(
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
    if (!selectedTag) return;
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

  const pinnedThesis: TopicThesisView | null = useMemo(() => {
    const targetId = selectedThesisId ?? view?.pinnedThesisId;
    if (!targetId || !view) return null;
    for (const c of view.cases) {
      const t = c.theses.find((th) => th.thesisId === targetId);
      if (t) return t;
    }
    return null;
  }, [selectedThesisId, view]);

  const totalThesisCount = view?.derivedFrom.thesisIds.length ?? 0;
  const pendingReviewCount = view
    ? view.cases.flatMap((c) => c.theses).filter((t) => !t.reviewOutcome).length
    : 0;

  function setSelectedTag(tag: string) {
    const next = new URLSearchParams(searchParams);
    next.set("tag", tag);
    setSearchParams(next, { replace: true });
  }

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
      {/* 顶部 banner：横跨三栏，左=标题+lede，右=3 个 meta 卡片（设计图 9） */}
      <header
        className="topic-list__banner"
        data-testid="topic-list-banner"
      >
        <div className="topic-list__banner-main">
          <p className="eyebrow">CROSS-CASE RESEARCH PROJECTION</p>
          <h1>{view?.tag ?? selectedTag} · 主题研究</h1>
          <p className="lede">
            从多个 ResearchCase 聚合命题、公司角色和披露持仓；
            每一项都可回链到冻结证据与人工审核记录。
          </p>
        </div>
        <dl className="topic-list__banner-meta">
          <BannerMeta
            label="证据截止"
            value={view ? view.cutoff.slice(0, 10) : "—"}
          />
          <BannerMeta
            label="历史口径"
            value={view?.isHistorical ? "历史截面" : "当前账本"}
            mono
          />
          <BannerMeta
            label="待复核关系"
            value={`${pendingReviewCount} 条`}
            warn={pendingReviewCount > 0}
          />
        </dl>
      </header>

      {/* 警告条 */}
      <PaperCard
        style={{
          borderLeft: "4px solid var(--warning)",
          background: "var(--paper-soft, #faf6ed)",
        }}
        data-testid="topic-list-warning"
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
            聚合投影，不构成主题级统一结论
          </p>
          <p
            className="muted"
            style={{
              fontSize: 12,
              margin: 0,
              textAlign: "right",
            }}
          >
            所有状态继承案例层原审核结果
          </p>
        </div>
      </PaperCard>

      <div className="topic-list__columns">
        {/* 左：主题目录（设计图 9 左 280，6 个主题 + 搜索 + tabs） */}
        <aside
          className="topic-list__directory"
          data-testid="topic-list-directory"
        >
          <div className="topic-list__directory-head">
            <p
              style={{
                fontSize: 13,
                fontWeight: 500,
                margin: 0,
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span>主题目录</span>
              <span className="muted">{items.length} 个主题</span>
            </p>
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
                      : "待审核"}
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
                      <div className="topic-list__dir-row">
                        <span className="topic-list__dir-name">{t.tag}</span>
                        {active && (
                          <span
                            className="muted"
                            style={{ fontSize: 11 }}
                          >
                            当前
                          </span>
                        )}
                      </div>
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

        {/* 中：主区（设计图 9 中部：3 张案例卡 + 2 个表 + 路径） */}
        <section
          className="topic-list__main"
          data-testid="topic-list-main"
        >
          {/* ResearchCase 与命题 3 张横排卡片 */}
          <div
            className="topic-list__case-grid"
            data-testid="topic-list-cases"
          >
            <div className="topic-list__case-grid-head">
              <p className="section-kicker">ResearchCase 与命题</p>
              <p className="muted" style={{ fontSize: 11 }}>
                {view?.cases.length ?? 0} 案例 · {totalThesisCount} 条有效命题
              </p>
            </div>
            {view?.cases.length === 0 ? (
              <p className="muted">该主题暂无参与案例。</p>
            ) : (
              <div className="topic-list__case-grid-cards">
                {view?.cases.map((c) => (
                    <CaseCard
                    key={c.caseId}
                    caseCard={c}
                    pinnedId={selectedThesisId ?? view.pinnedThesisId}
                    onSelectThesis={setSelectedThesisId}
                  />
                ))}
              </div>
            )}
          </div>

          {/* 公司 × 主题角色 表 */}
          <PaperCard data-testid="topic-list-roles">
            <div className="topic-list__section-head">
              <p className="section-kicker">公司 × 主题角色</p>
              <p className="muted" style={{ fontSize: 11 }}>
                角色为点时关系，不是公司级结论。
              </p>
            </div>
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
                  {view?.companyRoles.map((r, i) => (
                    <RoleRow
                      key={`${r.companyId}-${r.caseId ?? "na"}-${i}`}
                      role={r}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 披露基金暴露 表 */}
          <PaperCard data-testid="topic-list-exposure">
            <div className="topic-list__section-head">
              <p className="section-kicker">披露基金暴露</p>
              <p className="muted" style={{ fontSize: 11 }}>
                披露持仓，不代表当前持仓或推荐。
              </p>
            </div>
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
                    <FundExposureRow
                      key={`${p.fundId}-${p.stockId}-${p.reportPeriod}-${i}`}
                      position={p}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </PaperCard>

          {/* 主题关系路径 5 节点链 */}
          <PaperCard data-testid="topic-list-path">
            <div className="topic-list__section-head">
              <p className="section-kicker">主题关系路径</p>
              <p className="muted" style={{ fontSize: 11 }}>
                选择任一节点可检查来源（5 节点链：冻结证据 → 命题 →
                公司角色 → 股票映射 → 基金披露）。
              </p>
            </div>
            <div
              className="topic-list__path"
              data-testid="topic-list-path-chain"
            >
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

        {/* 右：固定证据检查器（设计图 9 右侧 320） */}
        <aside
          className="topic-list__inspector"
          data-testid="topic-list-inspector"
        >
          <PaperCard data-testid="topic-list-inspector-card">
            <div className="topic-list__inspector-head">
              <p
                className="eyebrow"
                style={{ margin: 0 }}
              >
                固定证据检查器
              </p>
              <span
                className="state-badge"
                data-testid="topic-inspector-status"
                style={{
                  color:
                    pinnedThesis?.reviewOutcome
                      ? "var(--reviewed, #1c1b18)"
                      : "var(--contradict, #c23a3a)",
                }}
              >
                {pinnedThesis?.reviewOutcome
                  ? REVIEW_LABEL[pinnedThesis.reviewOutcome] ?? "已复核"
                  : "反面证据"}
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
                  data-testid="topic-inspector-id"
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
                  data-testid="topic-inspector-title"
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
                    label="证据数量"
                    value={`支持 ${pinnedThesis.evidenceCounts?.supports ?? 0} · 反证 ${pinnedThesis.evidenceCounts?.contradicts ?? 0} · 上下文 ${pinnedThesis.evidenceCounts?.contextualizes ?? 0}`}
                  />
                  <InspectorRow
                    label="AI判断"
                    value={`${AI_LABEL[pinnedThesis.aiConclusion ?? ""] ?? "未评估"}${pinnedThesis.aiProvisional ? " · 草案" : ""}`}
                  />
                  <InspectorRow
                    label="人工状态"
                    value={pinnedThesis.reviewOutcome ? REVIEW_LABEL[pinnedThesis.reviewOutcome] ?? pinnedThesis.reviewOutcome : "待复核"}
                  />
                  <InspectorRow
                    label="来源时点"
                    value={pinnedThesis.evidence?.[0]?.sourceUrl ?? "冻结材料未提供 URL"}
                  />
                  <InspectorRow
                    label="证据范围"
                    value={pinnedThesis.evidence?.[0] ? JSON.stringify(pinnedThesis.evidence[0].locator) : "—"}
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
                  }}
                >
                  {pinnedThesis.evidence?.[0]?.statement ?? "当前命题暂无可展示的证据陈述。"}
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
                <span style={{ color: "var(--ink-strong, #1c1b18)" }}>
                  已人工复核 · {pinnedThesis.reviewer}
                </span>
                <br />
                {pinnedThesis.reviewReason ??
                  "关系、角色和适用范围已纳入冻结快照；后续更正如追加新版本。"}
              </p>
            )}
          </PaperCard>
          <PaperCard style={{ marginTop: 12 }}>
            <p className="section-kicker">参与公司（mock）</p>
            <ul style={{ paddingLeft: 16, fontSize: 12, margin: 0 }}>
              {companies.slice(0, 4).map((c) => (
                <li key={c.id} style={{ marginBottom: 2 }}>
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
  caseCard,
  pinnedId,
  onSelectThesis,
}: {
  caseCard: TopicCaseView;
  pinnedId: string | null | undefined;
  onSelectThesis: (id: string) => void;
}) {
  const primary = caseCard.theses[0];
  return (
    <article
      className="topic-list__case-card"
      data-testid={`topic-case-card-${caseCard.caseId}`}
    >
      <p
        className="muted"
        style={{ fontSize: 11, margin: 0 }}
      >
        {caseCard.caseId}
      </p>
      <h4 style={{ margin: "4px 0 8px", fontSize: 14, lineHeight: 1.3 }}>
        {primary?.title ?? caseCard.caseTitle}
      </h4>
      {caseCard.statusLabel && (
        <span
          className={`status-pill status-pill--${caseCard.statusVariant ?? "ai"}`}
          data-testid={`topic-case-status-${caseCard.caseId}`}
        >
          {caseCard.statusLabel}
        </span>
      )}
      {caseCard.summary && (
        <p
          style={{
            fontSize: 12,
            margin: "10px 0 6px",
            color: "var(--ink-soft)",
            lineHeight: 1.5,
          }}
        >
          {caseCard.summary}
        </p>
      )}
      {caseCard.rebuttalBullet && (
        <p
          className="topic-list__case-bullet"
          data-testid={`topic-case-rebuttal-${caseCard.caseId}`}
        >
          {caseCard.rebuttalBullet}
        </p>
      )}
      {caseCard.nextEventBullet && (
        <p
          className="topic-list__case-bullet"
          data-testid={`topic-case-next-${caseCard.caseId}`}
        >
          {caseCard.nextEventBullet}
        </p>
      )}
      {caseCard.theses.length > 0 && (
        <div
          style={{
            marginTop: 12,
            borderTop: "1px solid var(--line)",
            paddingTop: 10,
          }}
          data-testid={`topic-case-evidence-${caseCard.caseId}`}
        >
          <p className="section-kicker" style={{ marginBottom: 6 }}>
            证据维度
          </p>
          {caseCard.theses.map((thesis) => (
            <button
              key={thesis.thesisId}
              type="button"
              onClick={() => onSelectThesis(thesis.thesisId)}
              style={{ display: "block", width: "100%", textAlign: "left", marginBottom: 10, border: "0", borderBottom: "1px solid var(--line)", background: pinnedId === thesis.thesisId ? "var(--selected-surface)" : "transparent", color: "inherit", padding: "6px 4px", cursor: "pointer" }}
              data-testid={`topic-thesis-select-${thesis.thesisId}`}
            >
              <div style={{ fontSize: 12, fontWeight: 650 }}>
                {thesis.title ?? thesis.statement}
              </div>
              <div className="muted" style={{ fontSize: 11 }}>
                支持 {thesis.evidenceCounts?.supports ?? 0} · 反证 {thesis.evidenceCounts?.contradicts ?? 0} · 上下文 {thesis.evidenceCounts?.contextualizes ?? 0}
              </div>
              {(thesis.evidence ?? []).slice(0, 3).map((evidence) => (
                <div key={evidence.linkId} style={{ fontSize: 11, marginTop: 4 }}>
                  <span className={evidence.role === "contradicts" ? "muted-warn" : "muted"}>
                    {evidence.role === "supports" ? "支持" : evidence.role === "contradicts" ? "反证" : "范围"}
                  </span>{" "}
                  {evidence.statement}
                  <span className="muted"> · {evidence.reviewState === "machine_generated" ? "待人工复核" : evidence.reviewState}</span>
                </div>
              ))}
            </button>
          ))}
        </div>
      )}
      {primary?.aiConclusion && (
        <div
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            flexWrap: "wrap",
            fontSize: 11,
            marginTop: 8,
          }}
          data-testid={
            primary.thesisId === pinnedId
              ? `topic-case-pinned-${caseCard.caseId}`
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
    </article>
  );
}

function RoleRow({ role }: { role: TopicCompanyRoleView }) {
  return (
    <tr data-testid={`topic-role-row-${role.companyId}-${role.caseId ?? "na"}`}>
      <td>
        <Link
          to={`/companies/${encodeURIComponent(role.companyId)}`}
          className="prototype-link"
        >
          {role.companyName}
        </Link>
      </td>
      <td>{role.role}</td>
      <td>
        <span style={{ fontSize: 12 }}>
          {role.caseTitle ?? "—"}
          <br />
          <span className="muted" style={{ fontSize: 11 }}>
            {role.transmission ?? ""}
          </span>
        </span>
      </td>
      <td>
        {role.statusLabel && role.statusVariant ? (
          <span
            className={`status-pill status-pill--${role.statusVariant}`}
          >
            {role.statusLabel}
          </span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        <span className="muted" style={{ fontSize: 12 }}>
          {role.applicableScope ?? role.caseTitle ?? "—"}
        </span>
      </td>
    </tr>
  );
}

function FundExposureRow({
  position,
}: {
  position: TopicExposurePosition;
}) {
  return (
    <tr data-testid={`topic-fund-row-${position.fundId}`}>
      <td>
        <Link
          to={`/companies/${encodeURIComponent(position.stockId)}`}
          className="prototype-link"
        >
          {position.fundName}
        </Link>
      </td>
      <td>{position.reportPeriod}</td>
      <td>{position.weight.toFixed(1)}%</td>
      <td>
        {position.stockName}
        <br />
        <span className="muted" style={{ fontSize: 11 }}>
          {position.stockCode}
        </span>
      </td>
      <td>
        <span className="muted" style={{ fontSize: 11 }}>
          案例截止日可用
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
    <div className="topic-list__banner-cell">
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

function StatusBadge({
  variant,
  children,
}: {
  variant: "ai" | "support" | "contradict" | "warning" | "reviewed" | "draft";
  children: React.ReactNode;
}) {
  return (
    <span className={`status-pill status-pill--${variant}`}>{children}</span>
  );
}
