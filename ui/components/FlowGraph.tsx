"use client";

import "@xyflow/react/dist/style.css";

import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";

import type { GraphEdge, Placed } from "@/lib/graph";

const COLORS: Record<string, string> = {
  COMPLETED: "var(--ok)",
  FAILED: "var(--bad)",
  RUNNING: "var(--accent)",
  READY: "var(--accent)",
  BLOCKED: "var(--wait)",
  CANCELLED: "var(--muted)",
};

/** A read-only React Flow rendering of a laid-out graph (ARCHITECTURE §22). */
export function FlowGraph({ nodes, edges }: { nodes: Placed[]; edges: GraphEdge[] }) {
  const flowNodes: Node[] = nodes.map((node) => ({
    id: node.id,
    position: { x: node.x, y: node.y },
    data: { label: node.status ? `${node.label} · ${node.status.toLowerCase()}` : node.label },
    style: {
      border: `1.5px solid ${(node.status && COLORS[node.status]) || "var(--line)"}`,
      background: "var(--panel)",
      color: "var(--ink)",
      fontSize: 12,
      width: 190,
    },
    draggable: false,
    connectable: false,
  }));
  const flowEdges: Edge[] = edges.map((edge, index) => ({
    id: `${edge.from}->${edge.to}-${index}`,
    source: edge.from,
    target: edge.to,
    label: edge.kind,
    animated: edge.kind === "prerequisite",
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return (
    <div className="graph">
      <ReactFlow nodes={flowNodes} edges={flowEdges} fitView nodesDraggable={false} proOptions={{ hideAttribution: true }}>
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
