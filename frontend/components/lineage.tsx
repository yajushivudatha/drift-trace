"use client";

import * as React from "react";

import { LabelChip } from "@/components/common";
import type { Selection } from "@/components/citation-graph";
import { Select } from "@/components/ui/primitives";
import type { Hop, Trace, TraceNode } from "@/lib/api";
import { labelInfo, toneVar } from "@/lib/labels";
import { cn } from "@/lib/utils";

type Path = Hop[];

/** Every route from the queried claim to a leaf. A cycle ends a route. */
export function allPaths(trace: Trace): Path[] {
  const root = trace.nodes.find((node) => node.depth === 0);
  if (!root) return [];
  const outgoing = new Map<string, Hop[]>();
  trace.hops.forEach((hop) => outgoing.set(hop.from, [...(outgoing.get(hop.from) ?? []), hop]));
  const paths: Path[] = [];
  const walk = (node: string, path: Path, seen: Set<string>) => {
    const next = (outgoing.get(node) ?? []).filter((hop) => !seen.has(hop.to));
    if (!next.length) {
      if (path.length) paths.push(path);
      return;
    }
    next.forEach((hop) => walk(hop.to, [...path, hop], new Set([...seen, hop.to])));
  };
  walk(root.id, [], new Set([root.id]));
  return paths;
}

const nodeName = (node?: TraceNode) => node?.paper?.short_cite ?? node?.label ?? "Unknown source";

export function Lineage({ trace, selection, onSelect }: { trace: Trace; selection: Selection; onSelect: (s: Selection) => void }) {
  const paths = React.useMemo(() => allPaths(trace), [trace]);
  const nodes = React.useMemo(() => new Map(trace.nodes.map((n) => [n.id, n])), [trace.nodes]);
  const worstIndex = Math.max(0, paths.findIndex((path) => path.length === trace.worst_path.length && path.every((hop, i) => hop.id === trace.worst_path[i])));
  const [chosen, setChosen] = React.useState<number | null>(null);
  const index = chosen ?? worstIndex;
  const path = paths[index] ?? [];
  const root = trace.nodes.find((n) => n.depth === 0);

  // Selecting a hop or node in the graph moves the lineage onto a branch that contains it.
  React.useEffect(() => {
    if (!selection) return;
    const contains = (p: Path) => p.some((hop) => (selection.kind === "hop" ? hop.id === selection.id : hop.to === selection.id || hop.from === selection.id));
    if (contains(paths[index] ?? [])) return;
    const found = paths.findIndex(contains);
    if (found >= 0) setChosen(found);
  }, [selection, paths, index]);

  const steps = path.length ? path.map((hop) => nodes.get(hop.to)) : [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-line px-3">
        <h2 className="font-semibold">Lineage</h2>
        {paths.length > 1 && (
          <Select
            label="Choose a branch"
            className="h-7 min-w-0 flex-1 max-w-44 text-xs"
            value={String(index)}
            onChange={(value) => setChosen(Number(value))}
            options={paths.map((p, i) => ({ value: String(i), label: `${i === worstIndex ? "Worst path" : `Branch ${i + 1}`}: ${nodeName(nodes.get(p[p.length - 1].to))}` }))}
          />
        )}
      </div>
      <ol className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {root && (
          <Step
            node={root}
            text={root.claim_text ?? trace.claim}
            selected={selection?.kind === "node" && selection.id === root.id}
            onClick={() => onSelect({ kind: "node", id: root.id })}
          />
        )}
        {path.map((hop, i) => {
          const node = steps[i];
          const tone = toneVar(labelInfo(hop.primary_label).tone);
          const selected = selection?.kind === "hop" && selection.id === hop.id;
          return (
            <React.Fragment key={hop.id}>
              <li className="relative pl-[15px]">
                <div className="absolute bottom-0 left-[3px] top-0 w-[3px]" style={{ background: tone }} aria-hidden />
                <button
                  onClick={() => onSelect({ kind: "hop", id: hop.id })}
                  className={cn("my-0.5 ml-1 flex flex-wrap items-center gap-1 rounded-[4px] p-1 text-left hover:bg-sunken", selected && "bg-sunken ring-1 ring-accent")}
                  aria-label={`Inspect hop: ${labelInfo(hop.primary_label).text}`}
                >
                  <LabelChip label={hop.primary_label} />
                  {hop.secondary_labels.length > 0 && <span className="text-2xs text-ink-3">+{hop.secondary_labels.length} more</span>}
                </button>
              </li>
              {node && (
                <Step
                  node={node}
                  text={hop.source_quotes[0]?.text ?? node.claim_text ?? hop.explanation}
                  selected={selection?.kind === "node" && selection.id === node.id}
                  onClick={() => onSelect({ kind: "node", id: node.id })}
                />
              )}
            </React.Fragment>
          );
        })}
      </ol>
    </div>
  );
}

function Step({ node, text, selected, onClick }: { node: TraceNode; text: string; selected: boolean; onClick: () => void }) {
  return (
    <li className="relative">
      <button onClick={onClick} className={cn("flex w-full items-start gap-2 rounded-[5px] py-1.5 pr-1.5 text-left hover:bg-sunken", selected && "bg-sunken ring-1 ring-accent")}>
        <span className="mt-[5px] size-[9px] shrink-0 rounded-full border-2 border-ink bg-surface" aria-hidden />
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 font-semibold">
            {nodeName(node)}
            {node.is_origin && <span className="rounded-[3px] bg-ink px-1 text-[10px] font-bold leading-4 tracking-wide text-bg">ORIGIN</span>}
          </span>
          <span className="quote mt-0.5 line-clamp-3 block text-[13px] leading-[1.45] text-ink-2">{text}</span>
        </span>
      </button>
    </li>
  );
}
