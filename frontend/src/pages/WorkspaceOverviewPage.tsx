import { useSearchParams } from "react-router-dom";
import { researchClient } from "../data/researchClient";
import { useResearchQuery } from "../data/useResearchQuery";
import { WorkspaceOverview as Overview } from "../components/WorkspaceOverview";
import { HistoricalCutoffControl } from "../components/HistoricalCutoffControl";
import { PageStateBanners } from "../components/PageStateBanners";

export function WorkspaceOverviewPage() {
  const [search, setSearch] = useSearchParams();
  const cutoff = search.get("cutoff");

  function update(next: Record<string, string | null>): void {
    const sp = new URLSearchParams(search);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) sp.delete(k);
      else sp.set(k, v);
    }
    setSearch(sp, { replace: true });
  }

  const state = useResearchQuery(() => researchClient.getOverview({ cutoff: cutoff ?? undefined }), [cutoff]);

  return (
    <section className="page page--overview">
      <header className="page__header">
        <h1>研究总览</h1>
        <HistoricalCutoffControl
          cutoff={cutoff}
          onChange={(v) => update({ cutoff: v })}
        />
      </header>
      <PageStateBanners
        error={state.error}
        isHistorical={!!cutoff}
        writeDisabled={state.error?.kind === "backend_unavailable"}
      />

      {state.loading && !state.data && (
        <div className="skeleton skeleton--overview" aria-busy>
          <div className="skeleton__line skeleton__line--title" />
          <div className="skeleton__line" />
          <div className="skeleton__line" />
          <div className="skeleton__columns">
            <div className="skeleton__column" />
            <div className="skeleton__column" />
            <div className="skeleton__column" />
          </div>
        </div>
      )}

      {state.error?.kind === "backend_unavailable" && !state.data && (
        <p className="muted">
          后端不可用期间，仅可浏览已缓存的只读内容。
        </p>
      )}

      {state.data && <Overview data={state.data} />}
    </section>
  );
}