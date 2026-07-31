import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { researchClient } from "../data/researchClient";
import { useResearchQuery } from "../data/useResearchQuery";
import type { EvidenceRecord, ResearchCaseDossier } from "../domain/types";
import { ResearchCaseNavigator } from "../components/ResearchCaseNavigator";
import { ResearchCaseHeader } from "../components/ResearchCaseHeader";
import { ThesisHeader } from "../components/ThesisHeader";
import { ResearchDossier } from "../components/ResearchDossier";
import { CausalChainHorizontal } from "../components/CausalChainHorizontal";
import { EvidenceComparison } from "../components/EvidenceComparison";
import { SourceInspector } from "../components/SourceInspector";
import { HistoricalCutoffControl } from "../components/HistoricalCutoffControl";
import { PageStateBanners } from "../components/PageStateBanners";

const DEFAULT_TABS = [
  "研究摘要",
  "关键图表",
  "核心观点",
  "风险与假设",
  "相关公司",
  "研究日志",
];

const THESIS_VIEWS = ["档案", "关系图", "预测与证伪", "股票与基金"];

export function ResearchCaseDossierPage() {
  const { caseId = "ai-compute" } = useParams();
  const [search, setSearch] = useSearchParams();

  const tab = search.get("tab") ?? "研究摘要";
  const thesisView = search.get("view") ?? "档案";
  const focusedEvidenceId = search.get("focus");
  const focusedStepId = search.get("step") ?? null;
  const cutoff = search.get("cutoff");

  function update(next: Record<string, string | null>): void {
    const sp = new URLSearchParams(search);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) sp.delete(k);
      else sp.set(k, v);
    }
    setSearch(sp, { replace: true });
  }

  const casesState = useResearchQuery<ResearchCaseDossier["theses"]>(
    () => researchClient.getCaseSummaries(),
    [caseId]
  );

  const dossierState = useResearchQuery<ResearchCaseDossier>(
    () => researchClient.getCaseDossier(caseId, { cutoff: cutoff ?? undefined }),
    [caseId, cutoff]
  );

  const dossier = dossierState.data;

  const focusedRecord: EvidenceRecord | null = useMemo(() => {
    if (!dossier || !focusedEvidenceId) return null;
    const all = [
      ...dossier.evidence.supports,
      ...dossier.evidence.contradicts,
      ...dossier.evidence.contextualizes,
    ];
    return all.find((r) => r.link_id === focusedEvidenceId) ?? null;
  }, [dossier, focusedEvidenceId]);

  if (dossierState.error?.kind === "permission_denied") {
    return (
      <section className="page page--dossier">
        <PageStateBanners
          error={dossierState.error}
          isHistorical={false}
        />
        <header className="page__header">
          <h1>行业研究</h1>
          <HistoricalCutoffControl
            cutoff={cutoff}
            onChange={(v) => update({ cutoff: v })}
          />
        </header>
        <p className="muted">
          你可以继续查看有权访问的研究上下文，但创建与审核操作已隐藏。
        </p>
      </section>
    );
  }

  if (dossierState.error?.kind === "backend_unavailable") {
    return (
      <section className="page page--dossier">
        <PageStateBanners
          error={dossierState.error}
          isHistorical={!!cutoff}
        />
        <header className="page__header">
          <h1>行业研究</h1>
          <HistoricalCutoffControl
            cutoff={cutoff}
            onChange={(v) => update({ cutoff: v })}
          />
        </header>
        <p className="muted">
          已缓存的只读内容仍然可见；写操作已禁用直到后端恢复。
        </p>
      </section>
    );
  }

  if (dossierState.loading && !dossier) {
    return (
      <section className="page page--dossier" aria-busy>
        <header className="page__header">
          <h1>行业研究</h1>
          <HistoricalCutoffControl
            cutoff={cutoff}
            onChange={(v) => update({ cutoff: v })}
          />
        </header>
        <div className="skeleton skeleton--dossier">
          <div className="skeleton__columns">
            <div className="skeleton__column" />
            <div className="skeleton__column skeleton__column--wide" />
            <div className="skeleton__column" />
          </div>
        </div>
      </section>
    );
  }

  if (!dossier) {
    return (
      <section className="page page--dossier">
        <p className="error" role="alert">
          案例加载失败：未知错误
        </p>
      </section>
    );
  }

  const isEmptyCase =
    dossier.evidence.supports.length === 0 &&
    dossier.evidence.contradicts.length === 0 &&
    dossier.causal_chain.length === 0;

  return (
    <section className="page page--dossier">
      <header className="page__header">
        <h1>行业研究</h1>
        <HistoricalCutoffControl
          cutoff={cutoff}
          onChange={(v) => update({ cutoff: v })}
        />
      </header>
      <PageStateBanners
        error={dossierState.error}
        isHistorical={!!cutoff}
      />
      <div className="dossier-layout">
        <ResearchCaseNavigator
          cases={casesState.data ?? []}
          selectedCaseId={dossier.case.id}
          onSelect={() => undefined}
        />

        <article className="dossier-main">
          <ResearchCaseHeader
            title={dossier.case.title}
            topic={dossier.case.topic}
            author={dossier.case.author}
            updatedAt={dossier.case.updated_at}
            assessment={dossier.assessment}
          />

          <nav className="dossier-tabs" aria-label="案例内导航">
            {(dossier.tabs.length ? dossier.tabs : DEFAULT_TABS).map((t) => (
              <button
                key={t}
                type="button"
                className={`dossier-tab${tab === t ? " is-active" : ""}`}
                aria-selected={tab === t}
                onClick={() => update({ tab: t })}
              >
                {t}
              </button>
            ))}
          </nav>

          {tab === "研究摘要" && (
            <ResearchDossier
              rationale={dossier.assessment.rationale}
              competitiveExplanations={dossier.competitive_explanations}
              gaps={dossier.gaps}
              log={dossier.log}
            />
          )}

          {isEmptyCase && tab === "研究摘要" && (
            <section
              className="empty-case"
              data-testid="empty-case"
              aria-label="首次使用"
            >
              <h3>开始第一个研究案例</h3>
              <ol>
                <li>建立首个命题：例如"GPU 需求将增长"。</li>
                <li>导入资料：公告、财报、研报或行业资料均可。</li>
                <li>等待审核：AI 提议的证据链将进入审核队列，由人工确认。</li>
              </ol>
              <p className="muted">
                AI 临时判断默认不可信，必须经人工确认才会成为正式结论。
              </p>
            </section>
          )}

          {tab === "关键图表" && (
            <section className="dossier-pane">
              <p className="muted">关键图表占位（32 张）。</p>
            </section>
          )}
          {tab === "核心观点" && (
            <section className="dossier-pane">
              <p className="muted">核心观点占位（18 条）。</p>
            </section>
          )}
          {tab === "风险与假设" && (
            <section className="dossier-pane">
              <p className="muted">风险与假设占位（12 条）。</p>
            </section>
          )}
          {tab === "相关公司" && (
            <section className="dossier-pane">
              <p className="muted">相关公司占位（48 家）。</p>
            </section>
          )}
          {tab === "研究日志" && (
            <section className="dossier-pane">
              <ol className="dossier__log">
                {dossier.log.map((entry) => (
                  <li key={entry.id}>
                    <time>{entry.at}</time>
                    <span>{entry.text}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}

          <nav className="dossier-tabs dossier-tabs--sub" aria-label="命题视图">
            {THESIS_VIEWS.map((v) => (
              <button
                key={v}
                type="button"
                className={`dossier-tab${thesisView === v ? " is-active" : ""}`}
                aria-selected={thesisView === v}
                onClick={() => update({ view: v })}
              >
                {v}
              </button>
            ))}
          </nav>

          {thesisView === "档案" && (
            <>
              <section className="dossier-pane dossier-pane--causal">
                <header className="pane-header">
                  <h2>因果链（核心议题）</h2>
                  <button type="button">展开因果 ⌄</button>
                </header>
                <CausalChainHorizontal
                  steps={dossier.causal_chain}
                  focusedStepId={focusedStepId}
                  onSelect={(id) => update({ step: id || null })}
                />
              </section>

              {dossier.assessment.major_gap && !isEmptyCase && (
                <p className="dossier__major-gap" data-testid="major-gap">
                  主要阻塞：{dossier.assessment.major_gap}
                </p>
              )}

              <section className="dossier-pane dossier-pane--evidence">
                <EvidenceComparison
                  evidence={dossier.evidence}
                  focusedLinkId={focusedEvidenceId}
                  focusedStepId={focusedStepId}
                  onSelect={(id) => update({ focus: id })}
                />
              </section>
            </>
          )}

          {thesisView === "关系图" && (
            <section className="dossier-pane">
              <p className="muted">
                在关系模式中查看完整证据 → 公司 → 基金的连续画布。
              </p>
            </section>
          )}
          {thesisView === "预测与证伪" && (
            <section className="dossier-pane">
              <p className="muted">预测与证伪视图占位。</p>
            </section>
          )}
          {thesisView === "股票与基金" && (
            <section className="dossier-pane">
              <p className="muted">
                股票与基金映射占位。报告期 / 披露日 / 采集日将分别标注。
              </p>
            </section>
          )}
        </article>

        <SourceInspector
          record={focusedRecord}
          onClose={() => update({ focus: null })}
        />
      </div>
    </section>
  );
}