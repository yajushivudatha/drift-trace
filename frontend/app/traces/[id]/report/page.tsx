"use client";

import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { use } from "react";

import { ErrorBox, LabelChip, SampleTag, Skeleton } from "@/components/common";
import { Button } from "@/components/ui/button";
import { api, type Trace } from "@/lib/api";
import { scoreTone, toneVar } from "@/lib/labels";

function limitations(trace: Trace) {
  const name = (id: string) => {
    const node = trace.nodes.find((n) => n.id === id);
    return node?.paper?.short_cite ?? node?.label ?? "A source";
  };
  const items = new Set<string>();
  trace.hops.forEach((hop) => {
    if (hop.source_status === "ABSTRACT_ONLY") items.add(`${name(hop.to)}: only the abstract was available.`);
    else if (hop.primary_label === "UNVERIFIABLE") items.add(`${name(hop.to)}: ${hop.confidence_reason}`);
  });
  trace.nodes.forEach((node) => {
    if (node.stop_reason === "max_depth") items.add(`${name(node.id)}: the trace stopped at the depth limit before reaching an origin.`);
  });
  return items.size ? [...items] : ["Every source in this trace was read in full."];
}

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: trace, isLoading, error } = useQuery({ queryKey: ["trace", id], queryFn: () => api.trace(id) });
  if (isLoading) return <div className="mx-auto max-w-[680px] p-6"><Skeleton className="h-96" /></div>;
  if (error || !trace) return <div className="p-4"><ErrorBox message={(error as Error)?.message ?? "This trace could not be found."} /></div>;

  const name = (nodeId: string) => {
    const node = trace.nodes.find((n) => n.id === nodeId);
    return node?.paper?.short_cite ?? node?.label ?? "Unknown source";
  };
  const hops = [...trace.worst_path.map((h) => trace.hops.find((x) => x.id === h)!).filter(Boolean), ...trace.hops.filter((h) => !trace.worst_path.includes(h.id))];

  return (
    <main className="mx-auto max-w-[680px] bg-surface px-6 py-10 sm:px-10 print:max-w-none print:p-0">
      <div className="no-print mb-6 flex justify-end">
        <Button size="sm" onClick={() => window.print()}><Printer /> Print</Button>
      </div>
      {trace.is_sample && <div className="mb-3"><SampleTag /></div>}
      <blockquote className="quote text-[24px] font-medium leading-[1.3]">{trace.claim}</blockquote>
      <p className="mt-5 flex items-baseline gap-2">
        <span className="quote text-[40px] font-semibold leading-none tabular-nums" style={{ color: toneVar(scoreTone(trace.fidelity_score)) }}>{trace.fidelity_score ?? "—"}</span>
        <span className="text-ink-2">{trace.fidelity_score === null ? "Not scored" : "fidelity of 100"}</span>
      </p>
      <p className="mt-3 leading-relaxed">{trace.summary}</p>

      <h2 className="mt-9 text-[17px] font-semibold">Drift path</h2>
      <ol className="mt-3 space-y-6">
        {hops.map((hop, index) => (
          <li key={hop.id} className="break-inside-avoid">
            <h3 className="flex flex-wrap items-center gap-2 font-semibold">
              <span className="tabular-nums text-ink-3">{index + 1}.</span>
              {name(hop.from)} to {name(hop.to)}
              <LabelChip label={hop.primary_label} />
              <span className="font-normal text-ink-3">{hop.confidence} confidence</span>
            </h3>
            {hop.secondary_labels.length > 0 && <p className="mt-1 flex flex-wrap gap-1">{hop.secondary_labels.map((label) => <LabelChip key={label} label={label} size="sm" />)}</p>}
            <div className="quote mt-2 space-y-1.5 text-[15px] leading-[1.55]">
              <p><span className="font-sans text-xs font-semibold text-ink-2">Claimed </span>“{hop.claimed_quote}”</p>
              {hop.source_quotes.map((quote) => (
                <p key={quote.text}>
                  <span className="font-sans text-xs font-semibold text-ink-2">Source says </span>“{quote.text}”
                  {(quote.section || quote.page) && <span className="font-sans text-xs text-ink-3"> ({[quote.section, quote.page ? `p. ${quote.page}` : null].filter(Boolean).join(", ")})</span>}
                </p>
              ))}
            </div>
            <p className="mt-2 leading-relaxed text-ink-2">{hop.explanation}</p>
          </li>
        ))}
      </ol>

      <h2 className="mt-9 text-[17px] font-semibold">Methodology</h2>
      <p className="mt-2 leading-relaxed text-ink-2">
        DriftTrace locates the claim in the citing paper, retrieves the most relevant passages of each cited paper, and compares the two with a language model. Each quotation shown here was matched against the stored text of the paper. A verdict whose quotations could not be matched was discarded.
      </p>
      <p className="mt-2 leading-relaxed text-ink-2">{trace.score_formula}</p>

      <h2 className="mt-9 text-[17px] font-semibold">Limitations</h2>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-2">
        {limitations(trace).map((item) => <li key={item}>{item}</li>)}
      </ul>
    </main>
  );
}
