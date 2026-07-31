import { useEffect, useRef } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import type { EdgeKind, WorkbenchResponse } from "../types";

export const edgeStyleByKind: Record<
  EdgeKind,
  { lineColor: string; lineStyle: "solid" | "dashed" | "dotted" }
> = {
  evidence: { lineColor: "#2e7a48", lineStyle: "solid" },
  causal: { lineColor: "#6f7cff", lineStyle: "dashed" },
  theme_role: { lineColor: "#9a6a12", lineStyle: "solid" },
  holding: { lineColor: "#9a6a12", lineStyle: "dotted" },
};

interface Props {
  data: WorkbenchResponse;
  onSelectEvidence: (linkId: string) => void;
  onSelectNode: (nodeId: string) => void;
}

export function EvidenceGraph({ data, onSelectEvidence, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const elements: ElementDefinition[] = [
      ...data.graph.nodes.map((n) => ({
        data: { id: n.id, label: n.label, kind: n.kind },
      })),
      ...data.graph.edges.map((e) => ({
        data: { id: e.id, source: e.source, target: e.target, kind: e.kind },
      })),
    ];
    let cy: Core | null = null;
    try {
      cy = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "text-wrap": "wrap",
              "background-color": "#4a5568",
              color: "#fff",
              "font-size": "10px",
              width: 60,
              height: 60,
            },
          },
          {
            selector: 'edge[kind="evidence"]',
            style: {
              "line-color": "#2e7a48",
              "line-style": "solid",
              "target-arrow-color": "#2e7a48",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="causal"]',
            style: {
              "line-color": "#6f7cff",
              "line-style": "dashed",
              "target-arrow-color": "#6f7cff",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="theme_role"]',
            style: {
              "line-color": "#9a6a12",
              "line-style": "solid",
              "target-arrow-color": "#9a6a12",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="holding"]',
            style: {
              "line-color": "#9a6a12",
              "line-style": "dotted",
              "target-arrow-color": "#9a6a12",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
        ],
        layout: { name: "cose" },
      });
      cy.on("tap", "edge", (evt) => onSelectEvidence(evt.target.id()));
      cy.on("tap", "node", (evt) => onSelectNode(evt.target.id()));
    } catch {
      // jsdom 或无渲染环境下降级，证据按钮列表仍可用
    }
    return () => {
      cy?.destroy();
    };
  }, [data, onSelectEvidence, onSelectNode]);

  return (
    <div className="evidence-graph-wrap">
      <div
        ref={containerRef}
        className="evidence-graph"
        style={{ width: "100%", height: "480px", border: "1px solid #e2e8f0" }}
      />
      <div className="evidence-buttons" role="group" aria-label="证据列表">
        {data.evidence_drawer_records.map((r) => (
          <button
            key={r.link_id}
            type="button"
            onClick={() => onSelectEvidence(r.link_id)}
          >
            查看证据：{r.statement_text ?? r.reason}
          </button>
        ))}
      </div>
    </div>
  );
}
