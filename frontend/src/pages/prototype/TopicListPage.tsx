import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import type { TopicListItem, TopicView } from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

export function TopicListPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [items, setItems] = useState<TopicListItem[]>([]);
  const [query, setQuery] = useState<string>("");
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [preview, setPreview] = useState<TopicView | null>(null);
  const [previewState, setPreviewState] = useState<PageState>({
    kind: "ready",
  });

  useEffect(() => {
    let cancelled = false;
    researchClient
      .listThemes()
      .then((v) => {
        if (!cancelled) {
          setItems(v);
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

  const filtered = useMemo<TopicListItem[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((t) => t.tag.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => {
    if (!selectedTag && items.length > 0) {
      setSelectedTag(items[0].tag);
    }
  }, [items, selectedTag]);

  useEffect(() => {
    if (!selectedTag) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreviewState({ kind: "loading" });
    researchClient
      .getThemeView(selectedTag)
      .then((v) => {
        if (!cancelled) {
          setPreview(v);
          setPreviewState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setPreview(null);
          setPreviewState({ kind: "error", message: err.message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTag]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="topic-list-loading">
        <p>正在加载主题列表…</p>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="prototype-screen" data-testid="topic-list-error">
        <div className="form-error">
          主题列表加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  const empty = filtered.length === 0;

  return (
    <div
      className="prototype-screen topic-list-screen"
      data-testid="topic-list-screen"
    >
      <PageHeader
        title="主题研究（横切）"
        eyebrow="跨案例主题 · 聚合投影"
        lede="对共享同一主题标签的所有 ResearchCase 的聚合投影。主题层不存储任何主题级结论；每个聚合数字都携带 derived_from 引用列表，可继续下钻到案例层有效判断与冻结原文。"
        meta={
          <dl className="theme-meta-grid">
            <MetaCell label="主题数" value={String(items.length)} />
            <MetaCell label="筛选后" value={String(filtered.length)} />
            <MetaCell label="视图口径" value="只读 · 聚合投影" />
          </dl>
        }
      />

      <PaperCard>
        <p style={{ fontSize: 12, margin: 0 }}>
          ⚠ 主题视图是案例层判断的聚合投影，不构成主题级结论；
          任何聚合数字均可展开到 derived_from 明细。
        </p>
      </PaperCard>

      <div className="topic-list__columns">
        <section className="topic-list__table" data-testid="topic-list-table">
          <div className="topic-list__filter">
            <input
              type="search"
              value={query}
              placeholder="按标签过滤"
              onChange={(e) => setQuery(e.target.value)}
              data-testid="topic-list-filter-input"
            />
          </div>
          {empty ? (
            <PaperCard>
              <p className="muted">
                没有匹配的主题标签。当前可走 <Link to="/themes">主题驱动入口</Link>
                {" "}查看案例中心主题。
              </p>
            </PaperCard>
          ) : (
            <table className="prototype-table">
              <thead>
                <tr>
                  <th>主题标签</th>
                  <th>案例数</th>
                  <th>公司数</th>
                  <th>命题数</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t) => {
                  const active = t.tag === selectedTag;
                  return (
                    <tr
                      key={t.tag}
                      className={
                        active
                          ? "prototype-table__row is-selected"
                          : "prototype-table__row"
                      }
                      onClick={() => setSelectedTag(t.tag)}
                      data-testid={`topic-list-row-${encodeURIComponent(t.tag)}`}
                    >
                      <td>{t.tag}</td>
                      <td>{t.caseCount}</td>
                      <td>{t.companyCount}</td>
                      <td>{t.thesisCount}</td>
                      <td>
                        <Link
                          to={`/topics/${encodeURIComponent(t.tag)}`}
                          className="prototype-link"
                        >
                          视图 →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>

        <aside
          className="topic-list__inspector"
          data-testid="topic-list-inspector"
        >
          {previewState.kind === "loading" ? (
            <PaperCard>
              <p className="muted">正在加载主题预览…</p>
            </PaperCard>
          ) : previewState.kind === "error" || !preview ? (
            <PaperCard>
              <p className="muted">
                暂无预览
                {previewState.message ? `：${previewState.message}` : ""}
              </p>
            </PaperCard>
          ) : (
            <TopicPreviewCard view={preview} />
          )}
        </aside>
      </div>
    </div>
  );
}

function TopicPreviewCard({ view }: { view: TopicView }) {
  const caseCount = view.cases.length;
  const companyCount = new Set(view.companyRoles.map((r) => r.companyId)).size;
  const exposureCount = view.fundExposure.length;
  return (
    <PaperCard>
      <p className="section-kicker">主题预览</p>
      <h2>{view.tag}</h2>
      <p style={{ fontSize: 12 }}>
        案例 {caseCount} · 公司 {companyCount} · 持仓 {exposureCount}
      </p>
      <p className="muted" style={{ fontSize: 12 }}>
        证据截止 {view.cutoff.slice(0, 10)}
        {view.isHistorical ? " · 历史回放" : ""}
      </p>
      {view.cases.length > 0 && (
        <>
          <p className="section-kicker" style={{ marginTop: 12 }}>
            参与案例
          </p>
          <ul style={{ paddingLeft: 16 }}>
            {view.cases.slice(0, 4).map((c) => (
              <li key={c.caseId} style={{ fontSize: 12 }}>
                {c.caseTitle}{" "}
                <span className="muted">
                  · 命题 {c.theses.length}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p style={{ marginTop: 12, fontSize: 12 }}>
        <Link
          to={`/topics/${encodeURIComponent(view.tag)}`}
          className="prototype-link"
        >
          查看完整主题视图 →
        </Link>
      </p>
    </PaperCard>
  );
}

function MetaCell({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="theme-meta-grid__cell">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
