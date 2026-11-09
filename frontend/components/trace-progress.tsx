"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Square } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { CitationGraph } from "@/components/citation-graph";
import { ErrorBox } from "@/components/common";
import { Button, buttonVariants } from "@/components/ui/button";
import { API_URL, api, type Trace } from "@/lib/api";
import { formatTime, usd } from "@/lib/utils";

interface LiveEvent {
  id: number;
  kind: string;
  message: string;
  at: string;
  data: { calls?: number; cost_usd?: number };
}

export function TraceProgress({ trace }: { trace: Trace }) {
  const client = useQueryClient();
  const [events, setEvents] = React.useState<LiveEvent[]>([]);
  const [cost, setCost] = React.useState({ calls: trace.llm_calls, usd: trace.cost_usd });
  const logRef = React.useRef<HTMLOListElement>(null);
  const finished = trace.status === "failed" || trace.status === "cancelled";

  React.useEffect(() => {
    const source = new EventSource(`${API_URL}/traces/${trace.id}/events`);
    source.onmessage = (message) => {
      const event: LiveEvent = JSON.parse(message.data);
      setEvents((current) => (current.some((e) => e.id === event.id) ? current : [...current, event]));
      if (event.kind === "cost") setCost({ calls: event.data.calls ?? 0, usd: event.data.cost_usd ?? 0 });
      if (["node", "hop", "status"].includes(event.kind)) client.invalidateQueries({ queryKey: ["trace", trace.id] });
    };
    source.addEventListener("end", () => {
      source.close();
      client.invalidateQueries({ queryKey: ["trace", trace.id] });
    });
    return () => source.close();
  }, [trace.id, client]);

  React.useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  const cancel = useMutation({
    mutationFn: () => api.cancel(trace.id),
    onSuccess: () => client.invalidateQueries({ queryKey: ["trace", trace.id] }),
  });

  return (
    <main className="flex h-[calc(100dvh-3rem)] flex-col">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line bg-surface px-4 py-3">
        <blockquote className="quote min-w-0 flex-1 basis-80 border-l-[3px] border-accent pl-3 text-[17px] leading-snug">{trace.claim}</blockquote>
        <dl className="flex gap-5 text-xs" aria-live="polite">
          <div>
            <dt className="text-ink-3">Model calls</dt>
            <dd className="text-[15px] font-semibold tabular-nums">{cost.calls}</dd>
          </div>
          <div>
            <dt className="text-ink-3">Estimated cost</dt>
            <dd className="text-[15px] font-semibold tabular-nums">{usd(cost.usd)}</dd>
          </div>
        </dl>
        {!finished && (
          <Button onClick={() => cancel.mutate()} disabled={trace.status === "cancelling" || cancel.isPending}>
            <Square /> {trace.status === "cancelling" ? "Cancelling" : "Cancel trace"}
          </Button>
        )}
      </header>

      {finished && (
        <div className="border-b border-line bg-surface px-4 py-3">
          {trace.status === "failed" ? (
            <ErrorBox message={trace.error ?? "The trace stopped."} action={<Link href="/" className={buttonVariants({ size: "sm", className: "mt-2" })}>Start a new trace</Link>} />
          ) : (
            <p className="text-ink-2">This trace was cancelled. <Link className="text-accent underline underline-offset-2" href="/">Start a new trace</Link></p>
          )}
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-rows-[40%_1fr] lg:grid-cols-[minmax(320px,30fr)_70fr] lg:grid-rows-1">
        <section className="flex min-h-0 flex-col border-b border-line bg-surface lg:border-b-0 lg:border-r" aria-label="Progress log">
          <h2 className="flex h-11 shrink-0 items-center gap-2 border-b border-line px-3 font-semibold">
            {!finished && <Loader2 className="size-4 animate-spin text-accent motion-reduce:animate-none" aria-hidden />}
            Progress
          </h2>
          <ol ref={logRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-[13px]" aria-live="polite">
            {events.length === 0 && <li className="text-ink-2">Waiting for a worker to pick up this trace.</li>}
            {events.map((event) => (
              <li key={event.id} className="flex gap-2 py-0.5">
                <time className="shrink-0 tabular-nums text-ink-3">{formatTime(event.at)}</time>
                <span className={event.kind === "hop" ? "font-medium" : "text-ink-2"}>{event.message}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="min-h-0" aria-label="Citation graph as it builds">
          {trace.nodes.length ? (
            <CitationGraph trace={trace} selection={null} onSelect={() => undefined} compact />
          ) : (
            <p className="p-4 text-ink-2">Papers appear here as they are processed.</p>
          )}
        </section>
      </div>
    </main>
  );
}
