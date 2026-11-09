"use client";

import { useMutation } from "@tanstack/react-query";
import { Check, Download, FileText, Share2 } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { CitationGraph, type Selection } from "@/components/citation-graph";
import { LabelChip, SampleTag, ScoreBadge } from "@/components/common";
import { Inspector } from "@/components/inspector";
import { Lineage } from "@/components/lineage";
import { Button } from "@/components/ui/button";
import { Dropdown, DropdownContent, DropdownItem, DropdownTrigger, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { api, exportUrl, type Trace } from "@/lib/api";
import { LABEL_ORDER } from "@/lib/labels";

function worstHop(trace: Trace) {
  const onPath = trace.hops.filter((hop) => trace.worst_path.includes(hop.id));
  const pool = onPath.length ? onPath : trace.hops;
  return pool.reduce<Trace["hops"][number] | undefined>((best, hop) => (!best || hop.severity > best.severity ? hop : best), undefined);
}

function ShareButton({ trace }: { trace: Trace }) {
  const [copied, setCopied] = React.useState(false);
  const mutation = useMutation({
    mutationFn: () => api.share(trace.id),
    onSuccess: async ({ share_token }) => {
      await navigator.clipboard?.writeText(`${window.location.origin}/shared/${share_token}`).catch(() => undefined);
      setCopied(true);
      setTimeout(() => setCopied(false), 2400);
    },
  });
  return (
    <Button size="sm" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
      {copied ? <><Check /> Link copied</> : <><Share2 /> Share</>}
    </Button>
  );
}

export function TraceWorkspace({ trace, readOnly }: { trace: Trace; readOnly?: boolean }) {
  const [selection, setSelection] = React.useState<Selection>(() => {
    const hop = worstHop(trace);
    return hop ? { kind: "hop", id: hop.id } : null;
  });
  const present = LABEL_ORDER.filter((label) => trace.label_counts[label]);

  const lineage = <Lineage trace={trace} selection={selection} onSelect={setSelection} />;
  const graph = <CitationGraph trace={trace} selection={selection} onSelect={setSelection} />;
  const inspector = <Inspector trace={trace} selection={selection} readOnly={readOnly} />;

  return (
    <main className="flex h-[calc(100dvh-3rem)] flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-b border-line bg-surface px-4 py-2.5">
        <ScoreBadge score={trace.fidelity_score} formula={trace.score_formula} />
        <div className="min-w-0 flex-1 basis-80">
          <h1 className="quote line-clamp-2 text-[17px] font-medium leading-snug">{trace.claim}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {trace.is_sample && <SampleTag />}
            {present.map((label) => (
              <span key={label} className="inline-flex items-center gap-0.5">
                <LabelChip label={label} size="sm" />
                <span className="pr-1 text-2xs tabular-nums text-ink-2">{trace.label_counts[label]}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="no-print flex items-center gap-1.5">
          <Link href={readOnly ? "#" : `/traces/${trace.id}/report`} className={readOnly ? "hidden" : ""}>
            <Button size="sm" tabIndex={-1}><FileText /> Report</Button>
          </Link>
          {!readOnly && (
            <Dropdown>
              <DropdownTrigger asChild><Button size="sm"><Download /> Export</Button></DropdownTrigger>
              <DropdownContent>
                {(["md", "pdf", "json"] as const).map((format) => (
                  <DropdownItem key={format} asChild>
                    <a href={exportUrl(trace.id, format)} download>{{ md: "Markdown", pdf: "PDF", json: "JSON" }[format]}</a>
                  </DropdownItem>
                ))}
              </DropdownContent>
            </Dropdown>
          )}
          {!readOnly && <ShareButton trace={trace} />}
        </div>
      </header>

      {/* Three panes on desktop. Tabs below 1024px, where panes side by side would be unreadable. */}
      <div className="hidden min-h-0 flex-1 lg:grid lg:grid-cols-[25fr_45fr_30fr]">
        <aside className="min-h-0 border-r border-line bg-surface" aria-label="Lineage">{lineage}</aside>
        <section className="min-h-0 bg-bg" aria-label="Citation graph">{graph}</section>
        <aside className="min-h-0 min-w-0 border-l border-line bg-surface" aria-label="Inspector">{inspector}</aside>
      </div>
      <Tabs defaultValue="evidence" className="flex min-h-0 flex-1 flex-col bg-surface lg:hidden">
        <TabsList>
          <TabsTrigger value="lineage">Lineage</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
        </TabsList>
        <TabsContent value="lineage" className="min-h-0 flex-1">{lineage}</TabsContent>
        <TabsContent value="graph" className="min-h-0 flex-1">{graph}</TabsContent>
        <TabsContent value="evidence" className="min-h-0 flex-1">{inspector}</TabsContent>
      </Tabs>
    </main>
  );
}
