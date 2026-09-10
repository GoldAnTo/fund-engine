import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { GraphNode } from "../domain/types";
import { GraphNodeCard } from "./GraphNodeCard";

export interface GraphFlowNodeData extends Record<string, unknown> {
  node: GraphNode;
  selected?: boolean;
  onSelect?: (id: string) => void;
}

// React Flow custom node: wraps our existing GraphNodeCard so the visual
// language stays identical to the previous canvas-grid layout, but the
// node is now draggable and pannable thanks to React Flow's machinery.
function GraphFlowNodeImpl({ data }: NodeProps) {
  const d = data as GraphFlowNodeData;
  return (
    <div className="react-flow__node-card">
      <Handle type="target" position={Position.Left} className="react-flow__handle" />
      <GraphNodeCard
        node={d.node}
        selected={d.selected}
        onClick={() => d.onSelect?.(d.node.id)}
      />
      <Handle type="source" position={Position.Right} className="react-flow__handle" />
    </div>
  );
}

export const GraphFlowNode = memo(GraphFlowNodeImpl);