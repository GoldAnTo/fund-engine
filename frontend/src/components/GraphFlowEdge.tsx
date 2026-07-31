import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";

export interface GraphFlowEdgeData extends Record<string, unknown> {
  kind: "evidence" | "causal" | "theme_role" | "holding";
  role?: string;
}

const KIND_CLASS: Record<GraphFlowEdgeData["kind"], string> = {
  evidence: "edge-evidence",
  causal: "edge-causal",
  theme_role: "edge-theme_role",
  holding: "edge-holding",
};

// Custom edge used in the relationship canvas. We render a smooth bezier path
// using React Flow's helper, plus a coloured marker matching the edge kind.
function GraphFlowEdgeImpl({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const kindClass = KIND_CLASS[(data as GraphFlowEdgeData | undefined)?.kind ?? "evidence"];

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={`url(#marker-${kindClass})`}
        className={`canvas-edge ${kindClass}${
          selected ? " is-selected" : ""
        }`}
      />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: "none",
          }}
          className="react-flow__edge-label"
        />
      </EdgeLabelRenderer>
    </>
  );
}

export const GraphFlowEdge = memo(GraphFlowEdgeImpl);