import { useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { researchClient } from "../data/researchClient";
import { useResearchQuery } from "../data/useResearchQuery";
import type {
  GraphEdge,
  GraphNode,
  RelationshipGraph,
  ThesisAssessment,
} from "../domain/types";
import { HistoricalCutoffControl } from "../components/HistoricalCutoffControl";
import { SourceInspector } from "../components/SourceInspector";
import { PageStateBanners } from "../components/PageStateBanners";
import { ThesisHeader } from "../components/ThesisHeader";
import { Breadcrumb } from "../components/primitives/Breadcrumb";
import { Chip } from "../components/primitives/Chip";
import { Button } from "../components/primitives/Button";
import { RelationshipFlow } from "../components/RelationshipFlow";

const GROUP_ORDER: NonNullable<GraphNode["group"]>[] = [
  "evidence",
  "proposition",
  "causal",
  "company",
  "fund",
];

const GROUP_LABEL: Record<string, string> = {
  evidence: "证据",
  proposition: "命题",
  causal: "因果链",
  company: "公司",
  fund: "基金",
};

const SAMPLE_ASSESSMENT: ThesisAssessment = {
  id: "thesis-ev",
  thesis_id: "t-ev",
  conclusion: "supported",
  rationale:
    "2024–2026 年，全球新能源汽车净渗透持续提升，中国产业链具备结构性优势，盈利中枢上移。",
  bullets: [],
  gaps: [],
  provisional: false,
  review: null,
  major_gap: "缺头部车企跨城覆盖样本",
  status_label: "验证中",
  supply_chain_level: "供应链级",
  updated_at: "2024-05-31",
  confidence_label: "中高",
  focus_axes: ["政策驱动", "成本下降", "需求扩张", "供应链分化"],
};

export function RelationshipCanvasPage() {
  const { caseId = "ai-compute" } = useParams();
  const [search, setSearch] = useSearchParams();
  const cutoff = search.get("cutoff");
  const selectedNodeId = search.get("node");
  const selectedEdgeId = search.get("focus");

  function update(next: Record<string, string | null>): void {
    const sp = new URLSearchParams(search);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) sp.delete(k);
      else sp.set(k, v);
    }
    setSearch(sp, { replace: true });
  }

  const [groupFilter, setGroupFilter] = useState<Set<NonNullable<GraphNode["group"]>>>(
    new Set(GROUP_ORDER)
  );

  const state = useResearchQuery<RelationshipGraph>(
    () =>
      researchClient.getRelationshipGraph(caseId, {
        cutoff: cutoff ?? undefined,
      }),
    [caseId, cutoff]
  );

  const data = state.data;

  const visibleNodes = useMemo(
    () =>
      data?.nodes.filter(
        (n) => !n.group || groupFilter.has(n.group as NonNullable<GraphNode["group"]>)
      ) ?? [],
    [data, groupFilter]
  );

  const visibleEdges = useMemo(
    () =>
      data?.edges.filter(
        (e) =>
          visibleNodes.some((n) => n.id === e.source) &&
          visibleNodes.some((n) => n.id === e.target)
      ) ?? [],
    [data, visibleNodes]
  );

  if (state.error?.kind === "backend_unavailable" && !data) {
    return (
      <section className="page page--relationship">
        <PageStateBanners error={state.error} isHistorical={!!cutoff} />
        <p className="muted">
          后端不可用，关系图暂不可见。可先返回{" "}
          <a href="/">研究总览</a> 查看已缓存内容。
        </p>
      </section>
    );
  }

  if (state.loading && !data) {
    return (
      <section className="page page--relationship" aria-busy>
        <div className="skeleton skeleton--relationship">
          <div className="skeleton__columns">
            <div className="skeleton__column skeleton__column--wide" />
            <div className="skeleton__column" />
          </div>
        </div>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="page page--relationship">
        <p className="error">关系图加载失败。</p>
      </section>
    );
  }

  const selectedEdge: GraphEdge | undefined = visibleEdges.find(
    (e) => e.id === selectedEdgeId
  );
  const selectedNode: GraphNode | undefined = visibleNodes.find(
    (n) => n.id === selectedNodeId
  );

  const isLarge = data.nodes.length > 200;
  const isOffline = state.error?.kind === "backend_unavailable";

  return (
    <section className="page page--relationship">
      <header className="page__header relationship-header">
        <div>
          <Breadcrumb
            items={[
              { label: "研究空间", href: "/cases" },
              { label: data.case.title },
            ]}
          />
          <div className="relationship-header__title">
            <h1>{data.case.title}</h1>
            <Chip tone="moss" bordered size="xs">
              公开
            </Chip>
            <Button variant="bare" size="xs" type="button" aria-label="收藏">☆</Button>
            <Button
              variant="primary"
              size="sm"
              type="button"
              data-testid="canvas-new-relation"
            >
              + 新建关系
            </Button>
          </div>
          <nav className="relationship-tabs" aria-label="关系视图">
            {GROUP_ORDER.map((g) => (
              <button
                key={g}
                type="button"
                className={`relationship-tab group-${g} is-active`}
              >
                {GROUP_LABEL[g]}
              </button>
            ))}
          </nav>
        </div>
        <HistoricalCutoffControl
          cutoff={cutoff}
          onChange={(v) => update({ cutoff: v })}
        />
      </header>
      <PageStateBanners
        error={state.error}
        isHistorical={!!cutoff}
        writeDisabled={isOffline}
      />

      {isLarge && (
        <p className="muted" data-testid="large-graph-flag">
          关系图规模较大（{data.nodes.length} 节点 / {data.edges.length} 边），
          已启用切片渲染；可通过图例过滤对象类型。
        </p>
      )}

      <ThesisHeader thesis={SAMPLE_ASSESSMENT} />

      <div className="relationship-layout">
        <article className="relationship-canvas" data-testid="relationship-canvas">
          <header className="relationship-canvas__legend" aria-label="图例">
            {data.legend.map((l) => (
              <button
                key={l.id}
                type="button"
                data-testid={`legend-${l.group}`}
                className={`legend-item legend-${l.group} ${
                  groupFilter.has(l.group as NonNullable<GraphNode["group"]>)
                    ? "is-active"
                    : ""
                }`}
                aria-pressed={groupFilter.has(
                  l.group as NonNullable<GraphNode["group"]>
                )}
                onClick={() =>
                  setGroupFilter((prev) => {
                    const next = new Set(prev);
                    const g = l.group as NonNullable<GraphNode["group"]>;
                    if (next.has(g)) next.delete(g);
                    else next.add(g);
                    return next;
                  })
                }
              >
                {l.label}
              </button>
            ))}
            <span className="muted">共 {visibleNodes.length} 条</span>
          </header>

          <div className="relationship-canvas__viewport">
            <RelationshipFlow
              data={{ ...data, nodes: visibleNodes, edges: visibleEdges }}
              selectedNodeId={selectedNodeId}
              selectedEdgeId={selectedEdgeId}
              onSelectNode={(id) => update({ node: id })}
              onSelectEdge={(id) => update({ focus: id })}
            />
          </div>

          <footer className="relationship-canvas__controls">
            <span className="legend-item legend-evidence is-active">正问证据</span>
            <span className="legend-item">反问证据</span>
            <span>支持</span>
            <span>反证</span>
            <span>— 100% + ⌄</span>
            <Button variant="ghost" size="sm" type="button">恢复全图</Button>
          </footer>
        </article>

        <SourceInspector
          record={
            selectedEdge
              ? {
                  link_id: selectedEdge.id,
                  statement_id: selectedEdge.target,
                  statement_text:
                    visibleNodes.find((n) => n.id === selectedEdge.target)?.label ?? "",
                  statement_kind: "disclosed_fact",
                  span_id: null,
                  verbatim_text: null,
                  locator: null,
                  reason: selectedEdge.reason ?? "因果传导",
                  role:
                    (selectedEdge.role as
                      | "supports"
                      | "contradicts"
                      | "contextualizes"
                      | undefined) ?? "supports",
                  scope: {},
                  period: selectedEdge.report_period ?? null,
                  available_at:
                    selectedEdge.report_period ?? new Date().toISOString(),
                  review_state:
                    (selectedEdge.review_state as
                      | "machine_generated"
                      | "reviewed"
                      | "rejected"
                      | undefined) ?? "machine_generated",
                  source_label: selectedEdge.kind,
                  reliability: 0.8,
                }
              : null
          }
        />
      </div>

      {selectedNode && !selectedEdge && (
        <p className="relationship-status muted" aria-live="polite">
          已选中节点：{selectedNode.label}
        </p>
      )}
    </section>
  );
}