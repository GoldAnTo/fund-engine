import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { LibraryView } from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const REVIEW_LABEL: Record<string, string> = {
  reviewed: "已人工复核",
  pending_review: "待人工审核",
};

// 三个时间点字段背后的业务含义。给研究员快速对齐"为什么同一篇资料有三
// 个时间"，避免把"采集时间"误读成"发布日晚了好几天"之类的疑问。
const TIME_FIELD_HINT: Record<string, string> = {
  publishedLabel: "原文对外公开的时间，是证据链最早的时间点。",
  availableLabel: "我们系统能拿到该版本的时点；晚于发布时表示有采集/解析延迟。",
  acquiredLabel: "该 DocumentVersion 写入资料库的时点；用于追溯何时被纳入证据基础。",
};

export function LibraryScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<LibraryView | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState("");
  const [entityFilter, setEntityFilter] = useState("");
  const [reviewFilter, setReviewFilter] = useState("");
  const [reuseFilter, setReuseFilter] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractNotice, setExtractNotice] = useState<string | null>(null);
  const [sourceMetaOpen, setSourceMetaOpen] = useState(false);
  // 证据图谱"跳转原文"通过 /library?document=<id> 定位到指定文档
  const [searchParams] = useSearchParams();
  const requestedDocRef = useRef<string | null>(searchParams.get("document"));

  const loadView = useCallback(() => {
    return researchClient.getLibraryView().then((v) => {
      setView(v);
      setSelectedId((prev) => {
        if (v.documents.some((d) => d.id === prev)) return prev;
        const requested = requestedDocRef.current;
        if (requested && v.documents.some((d) => d.id === requested)) {
          return requested;
        }
        return v.selected.id;
      });
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadView()
      .then(() => {
        if (!cancelled) setState({ kind: "ready" });
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadView]);

  // 触发引擎 extract 步骤：对"有片段、无陈述"的待抽取版本运行 LLM 抽取
  // （append-only），完成后刷新视图以更新复用计数与待抽取标记。
  const runExtract = () => {
    const doc = view?.documents.find((d) => d.id === selectedId);
    if (!doc || extracting) return;
    setExtracting(true);
    setExtractNotice(null);
    researchClient
      .extractStatements(doc.id)
      .then((r) => {
        setExtractNotice(
          r.statementCount > 0
            ? `抽取完成：新增 ${r.statementCount} 条来源陈述。`
            : `抽取完成：未产生新陈述。${
                r.reason ? `原因：${r.reason}` : ""
              }`,
        );
        return loadView();
      })
      .catch((err: Error) => {
        setExtractNotice(`陈述抽取未完成：${err.message || "未知错误"}`);
      })
      .finally(() => setExtracting(false));
  };

  // 筛选器基于当前视图数据客户端过滤；选项从数据本身派生，避免写死。
  const typeOptions = useMemo(
    () => [...new Set((view?.documents ?? []).map((d) => d.documentType))],
    [view],
  );
  const entityOptions = useMemo(
    () => [...new Set((view?.documents ?? []).map((d) => d.entity))],
    [view],
  );
  const filteredDocs = useMemo(() => {
    const docs = view?.documents ?? [];
    return docs.filter(
      (d) =>
        (!typeFilter || d.documentType === typeFilter) &&
        (!entityFilter || d.entity === entityFilter) &&
        (!reviewFilter || d.reviewState === reviewFilter) &&
        (!reuseFilter ||
          (reuseFilter === "gte3"
            ? d.reuseCount >= 3
            : reuseFilter === "gte1"
              ? d.reuseCount >= 1
              : d.reuseCount === 0)),
    );
  }, [view, typeFilter, entityFilter, reviewFilter, reuseFilter]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="library-loading">
        <p>正在加载资料与知识…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="library-error">
        <div className="form-error">
          资料库加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  const selected =
    view.documents.find((d) => d.id === selectedId) ?? view.selected;

  return (
    <div className="prototype-screen" data-testid="library-screen">
      <header>
        <div className="eyebrow">资料与知识 · Library</div>
        <h1>冻结资料 · 已审核关系 · 知识复用</h1>
        <p className="lede">
          证据截止 {view.cutoff} · 快照 <code>{view.snapshotId}</code>
        </p>
      </header>

      <section className="prototype-library-filters" aria-label="筛选">
        <label>
          来源类型
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">全部</option>
            {typeOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          主体
          <select
            value={entityFilter}
            onChange={(e) => setEntityFilter(e.target.value)}
          >
            <option value="">全部</option>
            {entityOptions.map((en) => (
              <option key={en} value={en}>
                {en}
              </option>
            ))}
          </select>
        </label>
        <label>
          审核状态
          <select
            value={reviewFilter}
            onChange={(e) => setReviewFilter(e.target.value)}
          >
            <option value="">全部</option>
            <option value="reviewed">已人工复核</option>
            <option value="pending_review">待人工审核</option>
          </select>
        </label>
        <label>
          复用次数
          <select
            value={reuseFilter}
            onChange={(e) => setReuseFilter(e.target.value)}
          >
            <option value="">全部</option>
            <option value="gte3">≥ 3</option>
            <option value="gte1">≥ 1</option>
            <option value="eq0">0</option>
          </select>
        </label>
      </section>

      <div className="prototype-library-grid">
        <aside className="library-source-layer">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">资料列表</p>
              <h2>{filteredDocs.length} 份冻结资料</h2>
            </div>
          </div>
          <div className="prototype-library-source-list">
            {[...new Set(filteredDocs.map((d) => d.documentType))].map((group) => {
              const docs = filteredDocs.filter((d) => d.documentType === group);
              if (docs.length === 0) return null;
              return (
                <section key={group} className="library-source-group">
                  <header>
                    <strong>{group}</strong>
                    <span>{docs.length}</span>
                  </header>
                  {docs.map((d) => (
                    <a
                      key={d.id}
                      href={`#${d.id}`}
                      className={selected.id === d.id ? "is-selected" : ""}
                      onClick={(e) => {
                        e.preventDefault();
                        setSelectedId(d.id);
                      }}
                      title={`来源版本: ${d.sourceVersion}`}
                    >
                      <div className="library-row-top">
                        <span>
                          <strong>{d.entity}</strong>
                        </span>
                        <span
                          className="state-badge"
                          style={{
                            color:
                              d.reviewState === "reviewed"
                                ? "var(--support)"
                                : "var(--warning)",
                          }}
                        >
                          {REVIEW_LABEL[d.reviewState]}
                        </span>
                      </div>
                      <strong>{d.title}</strong>
                      <small>
                        发布 {d.publishedLabel} · 复用 ×{d.reuseCount}
                      </small>
                    </a>
                  ))}
                </section>
              );
            })}
          </div>
        </aside>

        <section className="library-reading-pane">
          {/* 主线：知识复用。先讲"这份资料在哪些案例里被怎么用"，再回原文。 */}
          <div
            className="prototype-paper"
            style={{ borderColor: "var(--support)" }}
          >
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">知识复用 · 主线</p>
                <h2>{selected.title}</h2>
              </div>
              <span className="state-badge reviewed">
                {REVIEW_LABEL[selected.reviewState]}
              </span>
            </div>
            {view.knowledge ? (
              <article>
                <p>
                  <strong>陈述：</strong>
                  {view.knowledge.statement.text}
                </p>
                <p>
                  <strong>关系：</strong>
                  {view.knowledge.roleLabel} · 关系 ID {view.knowledge.link.id}
                  {" "}
                  <small>
                    {view.knowledge.reviewedBy} · {view.knowledge.reviewedAt}
                  </small>
                </p>
                {view.knowledge.thesis && (
                  <p>
                    <strong>命题：</strong>
                    <code>{view.knowledge.thesis.id}</code> ·{" "}
                    {view.knowledge.thesis.title}
                  </p>
                )}
                {view.knowledge.factor && (
                  <p>
                    <strong>因素：</strong>
                    <code>{view.knowledge.factor.id}</code> ·{" "}
                    {view.knowledge.factor.label}
                  </p>
                )}
              </article>
            ) : (
              <p style={{ color: "var(--ink-muted)" }}>该资料暂无关联关系。</p>
            )}

            <h3 style={{ marginTop: 16, fontSize: 13 }}>
              复用历史（{selected.reuseCount} 次）
            </h3>
            <p
              style={{
                fontSize: 11,
                color: "var(--ink-muted)",
                margin: "2px 0 8px",
              }}
            >
              "复用次数"指这份资料被多少条证据关系引用；下面每一行是一次
              具体的引用：哪个案例 → 哪条关系 → 何时建立。
            </p>
            <ul style={{ paddingLeft: 16, margin: 0 }}>
              {selected.reuseHistory.map((r) => (
                <li key={`${r.caseId}-${r.reusedAt}`}>
                  <code>{r.caseId}</code> · {r.label} · {r.reusedAt}
                </li>
              ))}
              {selected.reuseHistory.length === 0 && (
                <li style={{ color: "var(--ink-muted)" }}>暂无复用记录</li>
              )}
            </ul>
          </div>

          {/* 入口：原文片段。需要查看、抽取陈述时回到这里。 */}
          <div className="prototype-library-inspector" style={{ marginTop: 12 }}>
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">原文片段 · 入口</p>
                <h2>{selected.sourceName}</h2>
              </div>
            </div>
            <p style={{ fontSize: 12, color: "var(--ink-soft)" }}>
              <code>{selected.sourceVersion}</code>
              {selected.previousVersion && selected.previousVersion !== "—" && (
                <>
                  {" · 前序版本："}
                  <code>{selected.previousVersion}</code>
                </>
              )}
            </p>

            {selected.pendingExtraction ? (
              <div
                style={{
                  marginBottom: 8,
                  padding: 10,
                  background: "var(--ai-soft, #fff7e6)",
                  borderRadius: 6,
                }}
              >
                <button
                  type="button"
                  className="prototype-button primary"
                  disabled={extracting}
                  onClick={runExtract}
                  data-testid="extract-button"
                >
                  {extracting ? "抽取中…" : "⚗ 抽取陈述"}
                </button>
                <p
                  style={{
                    fontSize: 11,
                    color: "var(--ink-muted)",
                    margin: "6px 0 0",
                  }}
                >
                  这一版本有 {selected.spanCount ?? 0} 段原文、{selected.statementCount ?? 0}{" "}
                  条陈述。点击后引擎会用 LLM 从原文片段里生成陈述（append-only：只增加，不修改、不删除；
                  不会改动原文）。
                </p>
              </div>
            ) : (
              <p
                style={{
                  fontSize: 11,
                  color: "var(--ink-muted)",
                  margin: "4px 0 8px",
                }}
              >
                已有 {selected.spanCount ?? 0} 段原文、{selected.statementCount ?? 0}{" "}
                条陈述；不需要再抽取。
              </p>
            )}
            {extractNotice ? (
              <p style={{ fontSize: 12 }} role="status">
                {extractNotice}
              </p>
            ) : null}

            <blockquote>"{selected.sourceExcerpt}"</blockquote>
            <p
              style={{
                fontSize: 11,
                color: "var(--ink-muted)",
                margin: "4px 0 0",
              }}
            >
              {/* exactSpan 是机读 locator 的 JSON，humanSpan 是派生出的"在哪一段"，
                  默认展示人话版本；如有需要，点开可看原始定位。 */}
              区段：{selected.humanSpan ?? selected.exactSpan}
            </p>
          </div>

          {/* AI 提议关系：仅在当前文档存在 AI 提议且未人工复核时出现。
              引导进入审核队列。 */}
          {view.proposal && (
            <div
              className="prototype-paper"
              style={{
                marginTop: 12,
                borderColor: "var(--ai-draft)",
              }}
            >
              <div className="prototype-section-header">
                <div>
                  <p className="section-kicker">AI 提议关系</p>
                  <h2>{view.proposal.roleLabel}</h2>
                </div>
                <span className="state-badge ai">未经人工复核</span>
              </div>
              <p>{view.proposal.statement.text}</p>
              <small>
                关系 ID {view.proposal.link?.id ?? "—"} ·{" "}
                <Link to="/review">进入审核队列 →</Link>
              </small>
            </div>
          )}

          {/* 来源详情：发布时间 / 可用时间 / 获取时间 / 主体 / 类型。
              折叠到最末，避免挤占主线。点开后看每个时间点的业务含义。 */}
          <div
            className="prototype-paper"
            style={{ marginTop: 12, fontSize: 12 }}
          >
            <div
              className="prototype-section-header"
              style={{ cursor: "pointer" }}
              onClick={() => setSourceMetaOpen((v) => !v)}
              role="button"
              aria-expanded={sourceMetaOpen}
            >
              <div>
                <p className="section-kicker">来源详情</p>
                <h2 style={{ fontSize: 14 }}>
                  {sourceMetaOpen ? "▾ 收起" : "▸ 展开"}
                </h2>
              </div>
              <span className="state-badge" style={{ color: "var(--ink-muted)" }}>
                {selected.entity} · {selected.documentType}
              </span>
            </div>
            {sourceMetaOpen && (
              <dl
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                  gap: 12,
                  marginTop: 8,
                  fontSize: 12,
                }}
              >
                {(
                  [
                    ["发布时间", selected.publishedLabel, "publishedLabel"],
                    ["可用时间", selected.availableLabel, "availableLabel"],
                    ["获取时间", selected.acquiredLabel, "acquiredLabel"],
                  ] as Array<[string, string, keyof typeof TIME_FIELD_HINT]>
                ).map(([label, value, key]) => (
                  <div key={label}>
                    <dt style={{ color: "var(--ink-muted)", fontWeight: 600 }}>
                      {label}
                    </dt>
                    <dd style={{ margin: "2px 0 4px" }}>{value}</dd>
                    <dd
                      style={{
                        margin: 0,
                        fontSize: 10,
                        color: "var(--ink-muted)",
                        lineHeight: 1.4,
                      }}
                    >
                      {TIME_FIELD_HINT[key]}
                    </dd>
                  </div>
                ))}
                <div>
                  <dt style={{ color: "var(--ink-muted)", fontWeight: 600 }}>
                    主体
                  </dt>
                  <dd style={{ margin: "2px 0 0" }}>{selected.entity}</dd>
                </div>
                <div>
                  <dt style={{ color: "var(--ink-muted)", fontWeight: 600 }}>
                    类型
                  </dt>
                  <dd style={{ margin: "2px 0 0" }}>{selected.documentType}</dd>
                </div>
              </dl>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
