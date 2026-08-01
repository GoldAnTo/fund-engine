import { useEffect, useState } from "react";
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

export function LibraryScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<LibraryView | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getLibraryView()
      .then((v) => {
        if (!cancelled) {
          setView(v);
          setSelectedId(v.selected.id);
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
          <select defaultValue="">
            <option value="">全部</option>
            <option>监管披露</option>
            <option>业绩说明会</option>
            <option>月度经营数据</option>
            <option>业绩公告</option>
          </select>
        </label>
        <label>
          主体
          <select defaultValue="">
            <option value="">全部</option>
            <option>Microsoft</option>
            <option>NVIDIA</option>
            <option>TSMC</option>
            <option>Broadcom</option>
          </select>
        </label>
        <label>
          审核状态
          <select defaultValue="">
            <option value="">全部</option>
            <option>已人工复核</option>
            <option>待人工审核</option>
          </select>
        </label>
        <label>
          复用次数
          <select defaultValue="">
            <option value="">全部</option>
            <option>≥ 3</option>
            <option>≥ 1</option>
            <option>0</option>
          </select>
        </label>
      </section>

      <div className="prototype-library-grid">
        <aside className="library-source-layer">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">资料列表</p>
              <h2>{view.documents.length} 份冻结资料</h2>
            </div>
            <span className="state-badge reviewed">DocumentVersion · SourceSpan</span>
          </div>
          <div className="prototype-library-source-list">
            {[...new Set(view.documents.map((d) => d.documentType))].map((group) => {
              const docs = view.documents.filter((d) => d.documentType === group);
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
                    >
                      <div className="library-row-top">
                        <span>
                          <strong>{d.entity}</strong>
                          <code style={{ marginLeft: 6, fontSize: 10 }}>
                            {d.sourceVersion}
                          </code>
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
                        发布 {d.publishedLabel} · 获取 {d.acquiredLabel}
                      </small>
                      <small>
                        复用 ×{d.reuseCount} · 链接案例 {d.linkedCaseIds.length}
                      </small>
                    </a>
                  ))}
                </section>
              );
            })}
          </div>
        </aside>

        <section className="library-reading-pane">
          <div className="prototype-library-inspector">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">阅读面板</p>
                <h2>{selected.title}</h2>
              </div>
              <span className="state-badge reviewed">
                {REVIEW_LABEL[selected.reviewState]}
              </span>
            </div>
            <p style={{ fontSize: 12, color: "var(--ink-soft)" }}>
              {selected.sourceName} · <code>{selected.sourceVersion}</code>
              （前序版本：<code>{selected.previousVersion}</code>）
            </p>
            <blockquote>
              "{selected.sourceExcerpt}"
            </blockquote>
            <p style={{ fontSize: 11, color: "var(--ink-muted)" }}>
              原文精确区段：{selected.exactSpan}
            </p>
            <dl
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: 8,
                marginTop: 12,
                fontSize: 11,
              }}
            >
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>发布时间</dt>
                <dd style={{ margin: 0 }}>{selected.publishedLabel}</dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>可用时间</dt>
                <dd style={{ margin: 0 }}>{selected.availableLabel}</dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>获取时间</dt>
                <dd style={{ margin: 0 }}>{selected.acquiredLabel}</dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>主体</dt>
                <dd style={{ margin: 0 }}>{selected.entity}</dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>类型</dt>
                <dd style={{ margin: 0 }}>{selected.documentType}</dd>
              </div>
              <div>
                <dt style={{ color: "var(--ink-muted)" }}>复用次数</dt>
                <dd style={{ margin: 0 }}>{selected.reuseCount}</dd>
              </div>
            </dl>
            <h3 style={{ marginTop: 16, fontSize: 13 }}>复用历史</h3>
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

          <div className="prototype-paper" style={{ marginTop: 12 }}>
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">知识复用</p>
                <h2>陈述 → 关系 → 命题 / 因素</h2>
              </div>
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
          </div>

          {view.proposal && (
            <div
              className="prototype-paper"
              style={{ marginTop: 12, borderColor: "var(--ai-draft)" }}
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
                关系 ID {view.proposal.link?.id ?? "—"} · 进入审核队列
              </small>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}