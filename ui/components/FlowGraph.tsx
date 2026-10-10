"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";

import type { GraphEdge, Placed } from "@/lib/graph";

const STATUS_COLOR: Record<string, string> = {
  COMPLETED: "var(--ok)",
  FAILED: "var(--bad)",
  RUNNING: "var(--a1)",
  READY: "var(--a1)",
  BLOCKED: "var(--wait)",
  WAITING_APPROVAL: "var(--wait)",
  CANCELLED: "var(--muted)",
};

/** A read-only React Flow rendering of a laid-out graph (ARCHITECTURE §22). */
export function FlowGraph({ nodes, edges }: { nodes: Placed[]; edges: GraphEdge[] }) {
  const flowNodes: Node[] = nodes.map((node) => ({
    id: node.id,
    position: { x: node.x, y: node.y },
    className: `fnode ${node.status ? `s-${node.status}` : "plain"}`,
    // the layout flows left to right (lib/graph.ts), so edges leave and enter sideways
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    data: {
      label: (
        <>
          <div className="fnode-label">{node.label}</div>
          {node.status ? (
            <div className="fnode-meta" style={{ ["--c" as string]: STATUS_COLOR[node.status] ?? "var(--dim)" }}>
              <i />
              {node.status.replaceAll("_", " ").toLowerCase()}
            </div>
          ) : null}
        </>
      ),
    },
    draggable: false,
    connectable: false,
  }));
  const flowEdges: Edge[] = edges.map((edge, index) => ({
    id: `${edge.from}->${edge.to}-${index}`,
    source: edge.from,
    target: edge.to,
    label: edge.kind,
    type: "smoothstep",
    animated: edge.kind === "prerequisite" || edge.kind === "planned",
    markerEnd: { type: MarkerType.ArrowClosed, color: "rgba(125, 211, 252, 0.7)", width: 16, height: 16 },
  }));
  return (
    <div className="graph hud">
      <ReactFlow
        key={nodes.map((node) => node.id).join()} // re-fit the view when a planner adds tasks
        nodes={flowNodes}
        edges={flowEdges}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
        maxZoom={1.6}
        nodesDraggable={false}
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} color="rgba(148, 163, 184, 0.22)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
