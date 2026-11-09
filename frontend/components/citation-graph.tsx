"use client";

import dagre from "@dagrejs/dagre";
import {
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  type Edge,
  type EdgeProps,
  getBezierPath,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { Ban, Filter, Route } from "lucide-react";
import * as React from "react";

import { FulltextBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Dropdown, DropdownCheckbox, DropdownContent, DropdownTrigger } from "@/components/ui/primitives";
import type { Hop, Trace, TraceNode } from "@/lib/api";
import { LABEL_ORDER, labelInfo, toneVar } from "@/lib/labels";
import { cn } from "@/lib/utils";

const NODE_WIDTH = 188;
const NODE_HEIGHT = 92;

export type Selection = { kind: "hop" | "node"; id: string } | null;

type PaperNodeData = { node: TraceNode; selected: boolean };
type DriftEdgeData = { hop: Hop; dimmed: boolean };

function PaperNode({ data }: NodeProps<Node<PaperNodeData>>) {
  const { node, selected } = data;
  const paper = node.paper;
  const name = paper ? paper.short_cite : node.label ?? "Unknown source";
  const title = paper?.title ?? (paper ? "Untitled" : node.depth === 0 ? "Free-text claim" : "Could not be matched to a paper");
  return (
    <div
      style={{ width: NODE_WIDTH }}
      className={cn(
        "overflow-hidden rounded-[6px] border bg-surface text-left",
        selected ? "border-accent ring-1 ring-accent" : "border-line",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !min-h-0 !min-w-0 !border-0 !bg-ink-3" isConnectable={false} />
      {paper?.is_retracted && (
        <div className="flex items-center gap-1 bg-d3 px-2 py-0.5 text-2xs font-semibold text-surface">
          <Ban className="size-3" aria-hidden /> Retracted
        </div>
      )}
      <div className="px-2.5 py-2">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-semibold">{name}</span>
          {node.is_origin && <span className="rounded-[3px] bg-ink px-1 text-[10px] font-bold leading-4 tracking-wide text-bg">ORIGIN</span>}
          <span className="ml-auto">{paper && <FulltextBadge status={paper.fulltext_status} compact />}</span>
        </div>
        <p className="mt-0.5 line-clamp-2 text-xs leading-snug text-ink-2">{title}</p>
        {paper?.venue && <p className="mt-0.5 truncate text-2xs text-ink-3">{paper.venue}</p>}
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5 !min-h-0 !min-w-0 !border-0 !bg-ink-3" isConnectable={false} />
    </div>
  );
}

function DriftEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }: EdgeProps<Edge<DriftEdgeData>>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const [hover, setHover] = React.useState(false);
  if (!data) return null;
  const info = labelInfo(data.hop.primary_label);
  const color = toneVar(info.tone);
  const width = { high: 3.5, medium: 2.25, low: 1.25 }[data.hop.confidence];
  const Icon = info.icon;
  return (
    <>
      <g style={{ color, opacity: data.dimmed ? 0.18 : 1 }} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <BaseEdge id={id} path={path} className="drift-edge-path" style={{ stroke: color, strokeWidth: selected ? width + 1 : width }} interactionWidth={22} />
      </g>
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan pointer-events-none absolute"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`, opacity: data.dimmed ? 0.18 : 1, zIndex: hover ? 20 : 1 }}
        >
          <span className="flex size-5 items-center justify-center rounded-full border border-line bg-surface" style={{ color }}>
            <Icon className="size-3" aria-hidden />
          </span>
          {hover && (
            <div className="absolute left-1/2 top-6 w-56 -translate-x-1/2 rounded-[5px] bg-ink px-2.5 py-1.5 text-xs leading-snug text-bg">
              <b>{info.text}</b>, {data.hop.confidence} confidence
              <span className="mt-0.5 block line-clamp-3 opacity-80">{data.hop.explanation}</span>
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { paper: PaperNode };
const edgeTypes = { drift: DriftEdge };

function layout(nodes: TraceNode[], hops: Hop[]): Record<string, { x: number; y: number }> {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 28, ranksep: 60, marginx: 12, marginy: 12 });
  nodes.forEach((node) => graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  hops.filter((hop) => !hop.is_cycle).forEach((hop) => graph.setEdge(hop.from, hop.to));
  dagre.layout(graph);
  return Object.fromEntries(nodes.map((node) => {
    const { x, y } = graph.node(node.id);
    return [node.id, { x: x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 }];
  }));
}

function Canvas({ trace, selection, onSelect, compact }: { trace: Trace; selection: Selection; onSelect: (s: Selection) => void; compact?: boolean }) {
  const [hidden, setHidden] = React.useState<Set<string>>(new Set());
  const [driftOnly, setDriftOnly] = React.useState(false);
  const { fitView } = useReactFlow();

  const positions = React.useMemo(() => layout(trace.nodes, trace.hops), [trace.nodes, trace.hops]);
  const present = LABEL_ORDER.filter((label) => trace.label_counts[label]);

  const pathHops = React.useMemo(() => new Set(trace.worst_path), [trace.worst_path]);
  const visibleHop = React.useCallback(
    (hop: Hop) => !hidden.has(hop.primary_label) && (!driftOnly || pathHops.has(hop.id)),
    [hidden, driftOnly, pathHops],
  );

  const nodes = React.useMemo<Node<PaperNodeData>[]>(() => {
    const onPath = new Set(trace.hops.filter((h) => pathHops.has(h.id)).flatMap((h) => [h.from, h.to]));
    return trace.nodes
      .filter((node) => !driftOnly || onPath.has(node.id))
      .map((node) => ({
        id: node.id,
        type: "paper",
        position: positions[node.id] ?? { x: 0, y: 0 },
        data: { node, selected: selection?.kind === "node" && selection.id === node.id },
        ariaLabel: `${node.paper?.short_cite ?? node.label ?? "Source"}${node.is_origin ? ", origin" : ""}${node.paper?.is_retracted ? ", retracted" : ""}. Press Enter for details.`,
        draggable: false,
      }));
  }, [trace.nodes, trace.hops, positions, selection, driftOnly, pathHops]);

  const edges = React.useMemo<Edge<DriftEdgeData>[]>(
    () =>
      trace.hops
        .filter((hop) => !driftOnly || pathHops.has(hop.id))
        .map((hop) => ({
          id: hop.id,
          source: hop.from,
          target: hop.to,
          type: "drift",
          data: { hop, dimmed: !visibleHop(hop) },
          selected: selection?.kind === "hop" && selection.id === hop.id,
          ariaLabel: `Citation hop: ${labelInfo(hop.primary_label).text}, ${hop.confidence} confidence. Press Enter for evidence.`,
        })),
    [trace.hops, selection, driftOnly, pathHops, visibleHop],
  );

  React.useEffect(() => {
    const timer = setTimeout(() => fitView({ padding: 0.12, duration: 220, maxZoom: 1.1 }), 60);
    return () => clearTimeout(timer);
  }, [fitView, nodes.length, driftOnly]);

  const toggle = (label: string, shown: boolean) =>
    setHidden((current) => {
      const next = new Set(current);
      if (shown) next.delete(label);
      else next.add(label);
      return next;
    });

  return (
    <div className="relative h-full w-full" role="application" aria-label="Citation graph. Tab between papers and citation hops, and press Enter to inspect one.">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.12, maxZoom: 1.1 }}
        minZoom={0.2}
        nodesConnectable={false}
        nodesDraggable={false}
        nodesFocusable
        edgesFocusable
        elementsSelectable
        onNodeClick={(_, node) => onSelect({ kind: "node", id: node.id })}
        onEdgeClick={(_, edge) => onSelect({ kind: "hop", id: edge.id })}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          const target = event.target as HTMLElement;
          const hopId = target.closest<HTMLElement>(".react-flow__edge")?.dataset.id;
          const nodeId = target.closest<HTMLElement>(".react-flow__node")?.dataset.id;
          if (hopId) onSelect({ kind: "hop", id: hopId });
          else if (nodeId) onSelect({ kind: "node", id: nodeId });
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} position="bottom-left" aria-label="Zoom controls" />
      </ReactFlow>

      {!compact && (
        <div className="absolute right-3 top-3 flex items-center gap-1.5">
          <Button size="sm" variant={driftOnly ? "primary" : "outline"} aria-pressed={driftOnly} onClick={() => setDriftOnly((v) => !v)}>
            <Route /> Show only drift path
          </Button>
          <Dropdown>
            <DropdownTrigger asChild>
              <Button size="sm"><Filter /> Filter{hidden.size ? ` (${hidden.size} hidden)` : ""}</Button>
            </DropdownTrigger>
            <DropdownContent>
              {present.map((label) => (
                <DropdownCheckbox key={label} checked={!hidden.has(label)} onChange={(shown) => toggle(label, shown)}>
                  {labelInfo(label).text}
                </DropdownCheckbox>
              ))}
            </DropdownContent>
          </Dropdown>
        </div>
      )}
    </div>
  );
}

export function CitationGraph(props: { trace: Trace; selection: Selection; onSelect: (s: Selection) => void; compact?: boolean }) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
