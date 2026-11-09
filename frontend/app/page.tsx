"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { ErrorBox, SampleTag, ScorePill, Skeleton } from "@/components/common";
import { NewTraceCard } from "@/components/new-trace";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";

function Specimen() {
  return (
    <ol className="quote mb-7 space-y-1 text-[clamp(17px,2.4vw,23px)] leading-[1.35]" aria-label="An example of a claim changing as it is cited">
      <li className="specimen-line border-l-[3px] border-d0 pl-3">
        Compound X reduced inflammation in <span className="mark-hedge">obese mice</span> at <span className="mark-hedge">high doses</span>.
      </li>
      <li className="specimen-line border-l-[3px] border-d1 pl-3">
        X <span className="mark-causal">reduces</span> inflammation.
      </li>
      <li className="specimen-line border-l-[3px] border-d3 pl-3">
        X <span className="mark-causal">treats</span> inflammatory disease in humans.
      </li>
    </ol>
  );
}

export default function Home() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const recent = useQuery({ queryKey: ["traces", "recent"], queryFn: () => api.traces({ per_page: 6 }) });

  return (
    <main className="mx-auto max-w-[760px] px-4 pb-16 pt-10 sm:pt-14">
      <Specimen />
      <h1 className="mb-5 text-[15px] leading-relaxed text-ink-2">
        <span className="font-semibold text-ink">Each citation looks reasonable. The last claim has no support.</span>{" "}
        DriftTrace follows a claim back to its source and shows where the meaning changed, with the exact text from both papers.
      </h1>

      {health.isError && <div className="mb-3"><ErrorBox message="The DriftTrace server is not responding. Start the backend, then reload this page." /></div>}
      {health.data && !health.data.llm_configured && (
        <p className="mb-3 rounded-[5px] bg-d1-soft px-3 py-2 text-d1">
          Add <code className="font-semibold">ANTHROPIC_API_KEY</code> and <code className="font-semibold">LLM_MODEL</code> to <code className="font-semibold">.env</code> and restart to run traces.
        </p>
      )}
      {health.data && health.data.llm_configured && !health.data.grobid && (
        <p className="mb-3 rounded-[5px] bg-d1-soft px-3 py-2 text-d1">
          GROBID is not running, so PDFs will be read as plain text and verdicts on them will carry lower confidence.
        </p>
      )}

      <NewTraceCard llmReady={health.data?.llm_configured ?? false} />

      <section className="mt-9" aria-label="Recent traces">
        <h2 className="mb-2 font-semibold">Recent traces</h2>
        {recent.isLoading && <div className="space-y-2"><Skeleton className="h-11" /><Skeleton className="h-11" /></div>}
        {recent.data?.items.length === 0 && <p className="text-ink-2">Traces you run appear here.</p>}
        <ul className="divide-y divide-line">
          {recent.data?.items.map((trace) => (
            <li key={trace.id}>
              <Link href={`/traces/${trace.id}`} className="flex items-center gap-3 py-2.5 hover:bg-sunken">
                <ScorePill score={trace.fidelity_score} />
                <span className="min-w-0 flex-1">
                  <span className="quote block truncate text-[14px]">{trace.claim}</span>
                  <span className="flex items-center gap-2 truncate text-xs text-ink-3">
                    {trace.paper?.title}
                    {trace.is_sample && <SampleTag />}
                    {trace.status !== "done" && <span className="capitalize">{trace.status}</span>}
                  </span>
                </span>
                <time className="shrink-0 text-xs tabular-nums text-ink-3">{formatDate(trace.created_at)}</time>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
