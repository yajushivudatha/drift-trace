"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ErrorBox, LabelChip, Skeleton } from "@/components/common";
import { QuoteDiff } from "@/components/inspector";
import { Button, buttonVariants } from "@/components/ui/button";
import { Select } from "@/components/ui/primitives";
import { api, reviewExportUrl, type ReviewItem } from "@/lib/api";
import { LABEL_ORDER, LABELS } from "@/lib/labels";

function ReviewCard({ item }: { item: ReviewItem }) {
  const client = useQueryClient();
  const [label, setLabel] = React.useState(item.feedback && !item.feedback.agrees ? item.feedback.user_label : "");
  const [note, setNote] = React.useState("");
  const mutation = useMutation({
    mutationFn: () => api.setGold(item.id, { gold_label: label, note: note || undefined }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["review"] }),
  });
  return (
    <article className="rounded-[6px] border border-line bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">System said</span>
        <LabelChip label={item.primary_label} />
        <span className="text-ink-2">{item.confidence[0].toUpperCase() + item.confidence.slice(1)} confidence</span>
        {item.feedback && !item.feedback.agrees && (<><span className="font-medium">User said</span><LabelChip label={item.feedback.user_label} /></>)}
        <Link href={`/traces/${item.trace_id}`} className="ml-auto text-accent underline underline-offset-2">Open trace</Link>
      </div>
      <QuoteDiff hop={item} citing={item.citing} cited={item.cited} />
      <p className="mt-2 leading-relaxed text-ink-2">{item.explanation}</p>
      {item.feedback?.note && <p className="mt-1 text-ink-2">User note: {item.feedback.note}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Select label="Gold label" placeholder="Set the gold label" className="w-56" value={label} onChange={setLabel} options={LABEL_ORDER.map((value) => ({ value, label: LABELS[value].text }))} />
        <input className="field max-w-xs flex-1 basis-44" placeholder="Reviewer note (optional)" aria-label="Reviewer note" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button variant="primary" disabled={!label || mutation.isPending} onClick={() => mutation.mutate()}>Save gold label</Button>
      </div>
      {mutation.isError && <div className="mt-2"><ErrorBox message={(mutation.error as Error).message} /></div>}
    </article>
  );
}

export default function ReviewPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["review"], queryFn: api.reviewQueue });
  return (
    <main className="mx-auto max-w-[900px] px-4 py-8">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-[20px] font-semibold">Review</h1>
        <a href={reviewExportUrl} download="gold-reviewed.jsonl" className={buttonVariants({ size: "sm" })}><Download /> Export gold labels</a>
      </div>
      <p className="mb-4 text-ink-2">Hops where a reader disagreed or the system had low confidence. The gold labels you set become the evaluation dataset.</p>
      {isLoading && <Skeleton className="h-64" />}
      {error && <ErrorBox message={(error as Error).message} />}
      {data?.items.length === 0 && <p className="rounded-[6px] border border-line bg-surface px-4 py-8 text-center text-ink-2">The queue is empty. Disagreements and low-confidence hops appear here.</p>}
      <div className="space-y-3">{data?.items.map((item) => <ReviewCard key={item.id} item={item} />)}</div>
    </main>
  );
}
