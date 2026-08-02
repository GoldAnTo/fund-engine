import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import type {
  CompanyDossierView,
  CompanyListItem,
  CompanyListView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const TYPE_LABEL: Record<string, string> = {
  listed: "已上市",
  unlisted: "未上市",
  otc: "柜台",
};

export function CompanyListPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<CompanyListView | null>(null);
  const [query, setQuery] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [preview, setPreview] = useState<CompanyDossierView | null>(null);
  const [previewState, setPreviewState] = useState<PageState>({
    kind: "ready",
  });

  useEffect(() => {
    let cancelled = false;
    researchClient
      .listCompanies()
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
  }, []);

  // 服务端实际过滤在后端 q 参数完成，前端实时仅在已加载项上做二次过滤，
  // 并非为大规模列表设计——mock 阶段足够，真实后端请用 q 参数。
  const filtered = useMemo<CompanyListItem[]>(() => {
    if (!view) return [];
    const q = query.trim().toLowerCase();
    if (!q) return view.items;
    return view.items.filter(
      (c) =>
        c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q),
    );
  }, [view, query]);

  useEffect(() => {
    if (!selectedId && view && view.items.length > 0) {
      setSelectedId(view.items[0].id);
    }
  }, [view, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreviewState({ kind: "loading" });
    researchClient
      .getCompanyDossier(selectedId)
      .then((d) => {
        if (!cancelled) {
          setPreview(d);
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
  }, [selectedId]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="company-list-loading">
        <p>正在加载公司列表…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="company-list-error">
        <div className="form-error">
          公司列表加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  const empty = filtered.length === 0;

  return (
    <div
      className="prototype-screen company-list-screen"
      data-testid="company-list-screen"
    >
      <PageHeader
        title="公司研究"
        eyebrow="公司档案 · 逆向视图"
        lede="以公司为入口的逆向读视图：跨案例主题角色、关联命题及判断、估值快照与基金披露持仓。所有结论均可下钻回案例层的 AIAssessment / ReviewDecision / SourceStatement。"
        meta={
          <dl className="theme-meta-grid">
            <MetaCell label="公司数" value={String(view.items.length)} />
            <MetaCell
              label="筛选后"
              value={String(filtered.length)}
            />
            <MetaCell label="视图口径" value="只读" />
          </dl>
        }
      />

      <div className="company-list__columns">
        <section className="company-list__table" data-testid="company-list-table">
          <div className="company-list__filter">
            <input
              type="search"
              value={query}
              placeholder="按代码 / 名称过滤"
              onChange={(e) => setQuery(e.target.value)}
              data-testid="company-list-filter-input"
            />
          </div>
          {empty ? (
            <PaperCard>
              <p className="muted">
                未匹配到公司。返回 <Link to="/themes">主题列表</Link> 或清除过滤条件。
              </p>
            </PaperCard>
          ) : (
            <table className="prototype-table">
              <thead>
                <tr>
                  <th scope="col">代码</th>
                  <th scope="col">名称</th>
                  <th scope="col">类型</th>
                  <th scope="col">股票</th>
                  <th scope="col">主题角色</th>
                  <th scope="col">最新披露期</th>
                  <th scope="col"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => {
                  const active = c.id === selectedId;
                  return (
                    <tr
                      key={c.id}
                      className={
                        active
                          ? "prototype-table__row is-selected"
                          : "prototype-table__row"
                      }
                      onClick={() => setSelectedId(c.id)}
                      data-testid={`company-list-row-${c.id}`}
                    >
                      <td>{c.code}</td>
                      <td>{c.name}</td>
                      <td>{TYPE_LABEL[c.type] ?? c.type}</td>
                      <td>{c.stockCount}</td>
                      <td>{c.themeRoleCount}</td>
                      <td>{c.latestReportPeriod ?? "—"}</td>
                      <td>
                        <Link
                          to={`/companies/${encodeURIComponent(c.id)}`}
                          className="prototype-link"
                        >
                          档案 →
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
          className="company-list__inspector"
          data-testid="company-list-inspector"
        >
          {previewState.kind === "loading" ? (
            <PaperCard>
              <p className="muted">正在加载档案预览…</p>
            </PaperCard>
          ) : previewState.kind === "error" || !preview ? (
            <PaperCard>
              <p className="muted">
                暂无预览
                {previewState.message ? `：${previewState.message}` : ""}
              </p>
            </PaperCard>
          ) : (
            <CompanyPreviewCard view={preview} />
          )}
        </aside>
      </div>
    </div>
  );
}

function CompanyPreviewCard({ view }: { view: CompanyDossierView }) {
  return (
    <PaperCard>
      <p className="section-kicker">公司预览</p>
      <h2>
        {view.company.name}{" "}
        <span className="muted">· {view.company.code}</span>
      </h2>
      <p className="muted" style={{ fontSize: 12 }}>
        类型：{TYPE_LABEL[view.company.type] ?? view.company.type}
      </p>
      <p style={{ fontSize: 12 }}>
        股票 {view.stocks.length} · 主题角色 {view.themeRoles.length} ·
        关联命题 {view.relatedTheses.length} · 估值{" "}
        {view.valuations.length} · 持仓 {view.fundHolders.length}
      </p>
      {view.themeRoles.length > 0 && (
        <>
          <p className="section-kicker" style={{ marginTop: 12 }}>
            主题角色
          </p>
          <ul style={{ paddingLeft: 16 }}>
            {view.themeRoles.slice(0, 3).map((r) => (
              <li key={r.id} style={{ fontSize: 12 }}>
                {r.caseTitle ?? "（未关联案例）"} · {r.role}
              </li>
            ))}
          </ul>
        </>
      )}
      {view.relatedTheses.length > 0 && (
        <>
          <p className="section-kicker" style={{ marginTop: 12 }}>
            关联命题
          </p>
          <ul style={{ paddingLeft: 16 }}>
            {view.relatedTheses.slice(0, 3).map((t) => (
              <li key={t.thesisId} style={{ fontSize: 12 }}>
                {t.title ?? t.statement.slice(0, 24)} ·{" "}
                {t.aiConclusion ?? "未评估"}
                {t.aiProvisional ? " · AI 草案" : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      <p style={{ marginTop: 12, fontSize: 12 }}>
        <Link
          to={`/companies/${encodeURIComponent(view.company.id)}`}
          className="prototype-link"
        >
          查看完整档案 →
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
