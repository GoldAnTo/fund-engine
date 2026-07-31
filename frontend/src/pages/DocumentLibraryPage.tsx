import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { researchClient } from "../data/researchClient";
import { useResearchQuery } from "../data/useResearchQuery";
import type { DocumentSpan, SourceDocumentView } from "../domain/types";
import { HistoricalCutoffControl } from "../components/HistoricalCutoffControl";
import { StatusMark } from "../components/StatusMark";
import { locatorText } from "../domain/locator";
import { PageStateBanners } from "../components/PageStateBanners";

export function DocumentLibraryPage() {
  const [search, setSearch] = useSearchParams();
  const cutoff = search.get("cutoff");
  const query = search.get("q") ?? "";

  function update(next: Record<string, string | null>): void {
    const sp = new URLSearchParams(search);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) sp.delete(k);
      else sp.set(k, v);
    }
    setSearch(sp, { replace: true });
  }

  const docsState = useResearchQuery<SourceDocumentView[]>(
    () => researchClient.getDocuments({ query, cutoff: cutoff ?? undefined }),
    [query, cutoff]
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [spans, setSpans] = useState<DocumentSpan[]>([]);

  // Refresh spans when a row is selected
  useMemo(() => {
    if (!selectedId) return;
    researchClient.getDocumentDetail(selectedId).then((d) => setSpans(d.spans));
  }, [selectedId]);

  const docs = docsState.data;
  const selected = docs?.find((d) => d.id === selectedId) ?? null;

  const noResultsByCutoff = !!cutoff && docs !== null && docs.length === 0;
  const noResultsByQuery = !!query && docs !== null && docs.length === 0;

  return (
    <section className="page page--library">
      <header className="page__header">
        <h1>证据库</h1>
        <HistoricalCutoffControl
          cutoff={cutoff}
          onChange={(v) => update({ cutoff: v })}
        />
      </header>
      <PageStateBanners
        error={docsState.error}
        isHistorical={!!cutoff}
        writeDisabled={docsState.error?.kind === "backend_unavailable"}
      />

      <div className="library-layout">
        <section
          className="library-table"
          aria-label="资料表格"
          data-testid="library-table"
        >
          <header className="library-table__filter">
            <input
              type="search"
              placeholder="搜索资料、来源、关联案例"
              value={query}
              onChange={(e) => update({ q: e.target.value || null })}
              aria-label="搜索资料"
              data-testid="library-search"
            />
            <span className="muted">{docs?.length ?? 0} 条</span>
          </header>
          {docsState.error && docsState.error.kind !== "backend_unavailable" && (
            <p className="error" role="alert">
              资料加载失败：{docsState.error.message}
            </p>
          )}
          {docsState.loading && !docs && (
            <div className="skeleton">
              <div className="skeleton__line skeleton__line--title" />
              <div className="skeleton__line" />
              <div className="skeleton__line" />
            </div>
          )}
          {docs && docs.length === 0 && (
            <p
              className="muted library-empty"
              data-testid="library-empty"
            >
              无匹配结果。
              {noResultsByCutoff && (
                <span> 当前历史截点后无可见资料。</span>
              )}
              {noResultsByQuery && !noResultsByCutoff && (
                <span> 可能是资料不存在，或权限、cutoff 导致不可见。</span>
              )}
            </p>
          )}
          {docs && docs.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th scope="col">标题</th>
                  <th scope="col">来源</th>
                  <th scope="col">类型</th>
                  <th scope="col">发布日期</th>
                  <th scope="col">解析</th>
                  <th scope="col">关联案例</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr
                    key={d.id}
                    className={selectedId === d.id ? "is-active" : ""}
                    onClick={() => setSelectedId(d.id)}
                    data-testid={`library-row-${d.id}`}
                  >
                    <td>
                      <span className="library-row__title">{d.title}</span>
                      <span className="library-row__version muted">
                        {d.version_label} · {d.span_count} 段
                      </span>
                    </td>
                    <td>{d.publisher}</td>
                    <td>{d.document_type}</td>
                    <td>{d.publish_date}</td>
                    <td>
                      <span data-quality={d.parse_quality}>
                        {d.parse_quality === "ok"
                          ? "完整"
                          : d.parse_quality === "partial"
                            ? "部分"
                            : "失败"}
                      </span>
                    </td>
                    <td>
                      {d.linked_cases.map((c) => (
                        <span key={c.id} className="tag">
                          {c.title}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <aside
          className="library-inspector"
          aria-label="资料检查器"
          data-testid="library-inspector"
        >
          {!selected && (
            <p className="muted">
              选择资料以查看冻结版本、解析质量与已关联案例。
            </p>
          )}
          {selected && (
            <>
              <h2>{selected.title}</h2>
              <dl className="library-inspector__meta">
                <div>
                  <dt>来源</dt>
                  <dd>{selected.publisher}</dd>
                </div>
                <div>
                  <dt>类型</dt>
                  <dd>{selected.document_type}</dd>
                </div>
                <div>
                  <dt>发布日</dt>
                  <dd>{selected.publish_date}</dd>
                </div>
                <div>
                  <dt>可访问</dt>
                  <dd>{selected.available_at}</dd>
                </div>
                <div>
                  <dt>采集</dt>
                  <dd>{selected.acquired_at}</dd>
                </div>
                <div>
                  <dt>解析器</dt>
                  <dd>{selected.parser_version}</dd>
                </div>
                <div>
                  <dt>解析质量</dt>
                  <dd data-quality={selected.parse_quality}>
                    {selected.parse_quality === "ok"
                      ? "完整"
                      : selected.parse_quality === "partial"
                        ? "部分"
                        : "失败"}
                    {selected.parse_failure_stage && (
                      <span className="muted">
                        （{selected.parse_failure_stage}）
                      </span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>状态</dt>
                  <dd>
                    {selected.parse_quality === "failed" ? (
                      <StatusMark status="parse_failed" />
                    ) : (
                      <StatusMark status="human_confirmed" />
                    )}
                  </dd>
                </div>
              </dl>

              {selected.parse_quality === "failed" ? (
                <section
                  className="library-inspector__failed"
                  data-testid="library-failed"
                >
                  <h3>解析失败</h3>
                  <p className="muted">
                    冻结版本仍然保留。失败阶段：
                    {selected.parse_failure_stage ?? "未知"}。可重试或更换解析器。
                  </p>
                  <button type="button">重试解析</button>
                  <ul className="muted">
                    <li>受影响引用：0（已切分片段全部保留）</li>
                  </ul>
                </section>
              ) : (
                <section className="library-inspector__spans">
                  <h3>原文片段</h3>
                  <ul>
                    {spans.map((s) => (
                      <li key={s.id}>
                        <span className="library-inspector__locator">
                          {locatorText(s.locator)}
                        </span>
                        <blockquote>{s.verbatim_text}</blockquote>
                        <span className="muted">
                          被 {s.cited_by.length} 条证据引用
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </aside>
      </div>
    </section>
  );
}