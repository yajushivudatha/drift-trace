"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, CircleSlash, ExternalLink, Minus, ThumbsDown, ThumbsUp, TriangleAlert, UserRound, X } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ErrorBox, FulltextBadge, LabelChip, Skeleton, VerifiedTag } from "@/components/common";
import type { Selection } from "@/components/citation-graph";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/primitives";
import { api, type FieldRow, type Hop, type PaperSummary, type Trace, type TraceNode } from "@/lib/api";
import { FIELD_NAMES, LABEL_ORDER, LABELS, STOP_REASONS } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** Split text into runs so each highlighted phrase gets its own mark. Longer phrases win overlaps. */
export function Highlighted({ text, marks }: { text: string; marks: { phrases?: string[]; className: string }[] }) {
  const spans: { start: number; end: number; className: string }[] = [];
  const lower = text.toLowerCase();
  marks
    .flatMap(({ phrases, className }) => (phrases ?? []).map((phrase) => ({ phrase, className })))
    .sort((a, b) => b.phrase.length - a.phrase.length)
    .forEach(({ phrase, className }) => {
      const start = lower.indexOf(phrase.toLowerCase());
      if (start < 0) return;
      const end = start + phrase.length;
      if (!spans.some((s) => start < s.end && end > s.start)) spans.push({ start, end, className });
    });
  spans.sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((span, i) => {
    parts.push(text.slice(cursor, span.start));
    parts.push(<span key={i} className={span.className}>{text.slice(span.start, span.end)}</span>);
    cursor = span.end;
  });
  parts.push(text.slice(cursor));
  return <>{parts}</>;
}

function where(location?: { section?: string | null; page?: number | null }) {
  return [location?.section, location?.page ? `p. ${location.page}` : null].filter(Boolean).join(", ");
}

export function QuoteDiff({ hop, citing, cited }: { hop: Hop; citing?: PaperSummary | null; cited?: PaperSummary | null }) {
  const h = hop.highlights ?? {};
  const legend = (h.source_hedges_removed?.length || h.source_conditions_dropped?.length || h.source_causal_terms?.length || h.claimed_causal_terms?.length) ? true : false;
  return (
    <section aria-label="What was claimed and what the source says">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[5px] border border-line bg-line">
        <div className="bg-surface p-3">
          <h3 className="text-xs font-semibold">Claimed <span className="font-normal text-ink-3">in {citing?.short_cite ?? "the claim as given"}</span></h3>
          <blockquote className="quote mt-1.5 text-[14px] leading-[1.55]">
            <Highlighted text={hop.claimed_quote} marks={[{ phrases: h.claimed_causal_terms, className: "mark-causal" }]} />
          </blockquote>
          {where(hop.claimed_location) && <p className="mt-1.5 text-2xs text-ink-3">{where(hop.claimed_location)}</p>}
        </div>
        <div className="bg-surface p-3">
          <h3 className="text-xs font-semibold">Source says <span className="font-normal text-ink-3">in {cited?.short_cite ?? "the source"}</span></h3>
          {hop.source_quotes.length === 0 ? (
            <p className="mt-1.5 text-ink-2">No quotation. {hop.confidence_reason}</p>
          ) : (
            hop.source_quotes.map((quote, index) => (
              <div key={index} className={index ? "mt-2.5 border-t border-line pt-2.5" : ""}>
                <blockquote className="quote mt-1.5 text-[14px] leading-[1.55]">
                  <Highlighted
                    text={quote.text}
                    marks={[
                      { phrases: h.source_hedges_removed, className: "mark-hedge" },
                      { phrases: h.source_conditions_dropped, className: "mark-condition" },
                      { phrases: h.source_causal_terms, className: "mark-causal" },
                    ]}
                  />
                </blockquote>
                {where(quote) && <p className="mt-1.5 text-2xs text-ink-3">{where(quote)}</p>}
              </div>
            ))
          )}
        </div>
      </div>
      {legend && (
        <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-ink-3">
          <span><span className="mark-hedge">hedge</span> lost in the claim</span>
          <span><span className="mark-condition">condition</span> dropped from the claim</span>
          <span><span className="mark-causal">verb</span> carries causal strength</span>
        </p>
      )}
    </section>
  );
}

const MATCH: Record<FieldRow["match"], { icon: typeof Check; text: string; className: string }> = {
  yes: { icon: Check, text: "Match", className: "text-d0" },
  partial: { icon: TriangleAlert, text: "Partial match", className: "text-d1" },
  no: { icon: X, text: "No match", className: "text-d3" },
};

export function FieldTable({ fields }: { fields: Record<string, FieldRow> }) {
  if (!Object.keys(fields).length) return null;
  return (
    <section>
      <h3 className="mb-1.5 text-xs font-semibold">Field by field</h3>
      <div className="overflow-x-auto rounded-[5px] border border-line">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-sunken text-left text-ink-2">
              <th scope="col" className="px-2 py-1 font-medium">Field</th>
              <th scope="col" className="px-2 py-1 font-medium">Claimed</th>
              <th scope="col" className="px-2 py-1 font-medium">Source</th>
              <th scope="col" className="px-1 py-1 font-medium"><span className="sr-only">Match</span></th>
            </tr>
          </thead>
          <tbody>
            {FIELD_NAMES.map(([key, name]) => {
              const row = fields[key];
              if (!row) return null;
              const match = MATCH[row.match];
              const Icon = match.icon;
              return (
                <tr key={key} className="border-t border-line align-top">
                  <th scope="row" className="px-2 py-1 text-left font-medium text-ink-2">{name}</th>
                  <td className="px-2 py-1">{row.claimed || <Minus className="size-3 text-ink-3" aria-label="Not stated" />}</td>
                  <td className="px-2 py-1">{row.source || <Minus className="size-3 text-ink-3" aria-label="Not stated" />}</td>
                  <td className="px-1 py-1"><Icon className={cn("size-3.5", match.className)} aria-label={match.text} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FeedbackBox({ hop, traceId }: { hop: Hop; traceId: string }) {
  const client = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [label, setLabel] = React.useState("");
  const [note, setNote] = React.useState("");
  const mutation = useMutation({
    mutationFn: (body: { agrees: boolean; user_label?: string; note?: string }) => api.feedback(hop.id, body),
    onSuccess: () => {
      setOpen(false);
      client.invalidateQueries({ queryKey: ["trace", traceId] });
    },
  });
  const fb = hop.feedback;
  return (
    <section>
      <h3 className="mb-1.5 text-xs font-semibold">Is this verdict right?</h3>
      {fb && !open ? (
        <p className="flex flex-wrap items-center gap-1.5 text-ink-2">
          {fb.agrees ? "You agreed with this verdict." : <>You corrected this to <LabelChip label={fb.user_label} /></>}
          <button className="text-accent underline underline-offset-2" onClick={() => setOpen(true)}>Change</button>
        </p>
      ) : (
        <div className="flex gap-1.5">
          <Button size="sm" disabled={mutation.isPending} onClick={() => mutation.mutate({ agrees: true })}><ThumbsUp /> Agree</Button>
          <Button size="sm" variant={open ? "primary" : "outline"} aria-expanded={open} onClick={() => setOpen((v) => !v)}><ThumbsDown /> Disagree</Button>
        </div>
      )}
      {open && (
        <form
          className="mt-2 space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate({ agrees: false, user_label: label, note: note || undefined });
          }}
        >
          <Select
            label="Correct label"
            placeholder="Choose the correct label"
            className="w-full"
            value={label}
            onChange={setLabel}
            options={LABEL_ORDER.map((value) => ({ value, label: LABELS[value].text }))}
          />
          <textarea className="field text-[13px]" rows={2} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
          <Button size="sm" variant="primary" type="submit" disabled={!label || mutation.isPending}>Save correction</Button>
        </form>
      )}
      {mutation.isError && <div className="mt-2"><ErrorBox message={(mutation.error as Error).message} /></div>}
    </section>
  );
}

function PaperLinks({ paper, quote }: { paper?: PaperSummary | null; quote?: string }) {
  if (!paper) return null;
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="font-medium">{paper.short_cite}:</span>
      <Link className="text-accent underline underline-offset-2" href={`/reader/${paper.id}${quote ? `?q=${encodeURIComponent(quote)}` : ""}`}>Open in reader</Link>
      {paper.doi && !paper.is_sample && (
        <a className="inline-flex items-center gap-0.5 text-accent underline underline-offset-2" href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">
          DOI <ExternalLink className="size-3" aria-hidden />
        </a>
      )}
    </span>
  );
}

function HopPanel({ hop, trace, readOnly }: { hop: Hop; trace: Trace; readOnly?: boolean }) {
  const from = trace.nodes.find((n) => n.id === hop.from);
  const to = trace.nodes.find((n) => n.id === hop.to);
  return (
    <div className="space-y-4 p-3">
      <QuoteDiff hop={hop} citing={from?.paper} cited={to?.paper} />
      <section>
        <div className="flex flex-wrap items-center gap-1.5">
          <LabelChip label={hop.primary_label} />
          {hop.secondary_labels.map((label) => <LabelChip key={label} label={label} size="sm" />)}
        </div>
        <p className="mt-2 leading-relaxed">{hop.explanation}</p>
        <p className="mt-2 text-xs text-ink-2">
          <span className="font-semibold text-ink">{hop.confidence[0].toUpperCase() + hop.confidence.slice(1)} confidence.</span> {hop.confidence_reason}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          {hop.quotes_verified && <VerifiedTag />}
          {hop.source_status && <FulltextBadge status={hop.source_status} />}
          {hop.is_self_citation && <span className="inline-flex items-center gap-1 text-2xs text-ink-2"><UserRound className="size-3" aria-hidden /> Self-citation</span>}
          {hop.is_cycle && <span className="inline-flex items-center gap-1 text-2xs text-ink-2"><CircleSlash className="size-3" aria-hidden /> Citation cycle</span>}
        </div>
      </section>
      <FieldTable fields={hop.field_comparison} />
      {!readOnly && <FeedbackBox hop={hop} traceId={trace.id} />}
      <section className="space-y-0.5 text-xs">
        <div><PaperLinks paper={from?.paper} quote={hop.claimed_quote} /></div>
        <div><PaperLinks paper={to?.paper} quote={hop.source_quotes[0]?.text} /></div>
      </section>
    </div>
  );
}

function NodePanel({ node }: { node: TraceNode }) {
  const { data, isLoading } = useQuery({ queryKey: ["paper", node.paper?.id], queryFn: () => api.paper(node.paper!.id), enabled: !!node.paper });
  const paper = node.paper;
  if (!paper) {
    return (
      <div className="space-y-2 p-3">
        <h3 className="font-semibold">{node.label}</h3>
        {node.claim_text && <blockquote className="quote text-[14px] leading-[1.55]">{node.claim_text}</blockquote>}
        {node.stop_reason && <p className="text-ink-2">{STOP_REASONS[node.stop_reason]}</p>}
      </div>
    );
  }
  return (
    <div className="space-y-3 p-3">
      {paper.is_retracted && (
        <div className="flex items-start gap-2 rounded-[5px] bg-d3 px-3 py-2 font-medium text-surface">
          <Ban className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{paper.retraction_note ?? "This paper is flagged as retracted."}</span>
        </div>
      )}
      <div>
        <h3 className="quote text-[16px] font-semibold leading-snug">{paper.title}</h3>
        <p className="mt-1 text-xs text-ink-2">{paper.authors.join(", ")}</p>
        <p className="text-xs text-ink-2">{[paper.venue, paper.year].filter(Boolean).join(", ")}</p>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-ink-3">Full text</dt>
        <dd><FulltextBadge status={paper.fulltext_status} />{paper.fulltext_source && paper.fulltext_source !== "sample" ? ` via ${paper.fulltext_source.replace("_", " ")}` : ""}</dd>
        <dt className="text-ink-3">Retraction</dt>
        <dd>{paper.is_retracted ? "Retracted or flagged" : "None found"}</dd>
        <dt className="text-ink-3">In this trace</dt>
        <dd>Cited {node.times_cited} {node.times_cited === 1 ? "time" : "times"}</dd>
        {node.stop_reason && <><dt className="text-ink-3">Trace ended</dt><dd>{STOP_REASONS[node.stop_reason] ?? node.stop_reason}</dd></>}
        {paper.parse_quality === "degraded" && <><dt className="text-ink-3">Parsing</dt><dd>Plain-text extraction, so confidence is lowered</dd></>}
        {paper.related_versions.map((version) => (
          <React.Fragment key={version.doi}>
            <dt className="text-ink-3">{version.relation === "has-preprint" ? "Preprint" : "Published"}</dt>
            <dd className="break-all">{version.doi}</dd>
          </React.Fragment>
        ))}
      </dl>
      {node.claim_text && (
        <section>
          <h4 className="mb-1 text-xs font-semibold">Claim in this paper</h4>
          <blockquote className="quote text-[14px] leading-[1.55]">{node.claim_text}</blockquote>
        </section>
      )}
      <section>
        <h4 className="mb-1 text-xs font-semibold">Abstract</h4>
        {isLoading ? <Skeleton className="h-20" /> : <p className="quote text-[13.5px] leading-[1.55] text-ink-2">{data?.abstract ?? "No abstract is available."}</p>}
      </section>
      <div className="text-xs"><PaperLinks paper={paper} /></div>
    </div>
  );
}

export function Inspector({ trace, selection, readOnly }: { trace: Trace; selection: Selection; readOnly?: boolean }) {
  const hop = selection?.kind === "hop" ? trace.hops.find((h) => h.id === selection.id) : undefined;
  const node = selection?.kind === "node" ? trace.nodes.find((n) => n.id === selection.id) : undefined;
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-11 shrink-0 items-center border-b border-line px-3">
        <h2 className="font-semibold">{hop ? "Evidence" : node ? "Paper" : "Inspector"}</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {hop && <HopPanel hop={hop} trace={trace} readOnly={readOnly} />}
        {node && <NodePanel node={node} />}
        {!hop && !node && <p className="p-3 text-ink-2">Select a hop or a paper to see its evidence.</p>}
      </div>
    </div>
  );
}
