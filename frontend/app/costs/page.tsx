"use client";

import { useQuery } from "@tanstack/react-query";

import { ErrorBox, Skeleton } from "@/components/common";
import { Tooltip } from "@/components/ui/primitives";
import { api, type CostStats } from "@/lib/api";
import { usd } from "@/lib/utils";

const PURPOSE: Record<string, string> = { extract: "Claim extraction", rerank: "Passage reranking", judge: "Judging" };

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <p className="text-xs text-ink-2">{label}</p>
      <p className="text-[26px] font-semibold leading-tight tabular-nums">{value}</p>
      {note && <p className="text-2xs text-ink-3">{note}</p>}
    </div>
  );
}

function DailySpend({ days }: { days: CostStats["by_day"] }) {
  const peak = Math.max(...days.map((d) => d.cost_usd), 0.0001);
  const recent = days.slice(-30);
  return (
    <figure>
      <figcaption className="mb-2 font-semibold">Spend per day</figcaption>
      <div className="flex h-36 items-end gap-[2px] border-b border-line" role="img" aria-label={`Bar chart of daily spend over ${recent.length} days. Daily figures are in the table below.`}>
        {recent.map((day) => (
          <Tooltip key={day.day} content={`${day.day}: ${usd(day.cost_usd)}, ${day.calls} calls`}>
            <div className="flex h-full max-w-10 flex-1 items-end" tabIndex={0}>
              <div className="w-full rounded-t-[4px] bg-accent" style={{ height: `${Math.max(2, (day.cost_usd / peak) * 100)}%` }} />
            </div>
          </Tooltip>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-2xs tabular-nums text-ink-3">
        <span>{recent[0]?.day}</span>
        <span>Peak {usd(peak)}</span>
        <span>{recent[recent.length - 1]?.day}</span>
      </div>
    </figure>
  );
}

export default function CostsPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["costs"], queryFn: api.costs });
  return (
    <main className="mx-auto max-w-[900px] px-4 py-8">
      <h1 className="mb-5 text-[20px] font-semibold">Costs</h1>
      {isLoading && <Skeleton className="h-64" />}
      {error && <ErrorBox message={(error as Error).message} />}
      {data && !data.pricing_configured && (
        <p className="mb-4 rounded-[5px] bg-d1-soft px-3 py-2 text-d1">Set LLM_PRICE_INPUT_PER_MTOK and LLM_PRICE_OUTPUT_PER_MTOK in .env to see costs in dollars.</p>
      )}
      {data && data.total_calls === 0 && <p className="rounded-[6px] border border-line bg-surface px-4 py-8 text-center text-ink-2">No model calls yet. Run a trace to see what it costs.</p>}
      {data && data.total_calls > 0 && (
        <div className="space-y-7">
          <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
            <Stat label="Total spend" value={usd(data.total_cost_usd)} />
            <Stat label="Average per trace" value={usd(data.avg_cost_per_trace)} />
            <Stat label="Model calls" value={data.total_calls.toLocaleString()} />
            <Stat label="Served from cache" value={`${Math.round((data.cache_hits / data.total_calls) * 100)}%`} note={`${data.cache_hits.toLocaleString()} calls at no cost`} />
          </div>

          <section>
            <h2 className="mb-2 font-semibold">By pipeline step</h2>
            <div className="overflow-x-auto rounded-[6px] border border-line bg-surface">
              <table className="w-full min-w-[560px] border-collapse text-right tabular-nums">
                <thead>
                  <tr className="border-b border-line text-xs text-ink-2">
                    {["Step", "Calls", "Cached", "Input tokens", "Output tokens", "Avg latency", "Cost"].map((h, i) => <th key={h} scope="col" className={`px-3 py-2 font-medium ${i === 0 ? "text-left" : ""}`}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {data.by_purpose.map((row) => (
                    <tr key={row.purpose} className="border-b border-line last:border-0">
                      <th scope="row" className="px-3 py-2 text-left font-medium">{PURPOSE[row.purpose] ?? row.purpose}</th>
                      <td className="px-3 py-2">{row.calls.toLocaleString()}</td>
                      <td className="px-3 py-2">{row.cache_hits.toLocaleString()}</td>
                      <td className="px-3 py-2">{row.input_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2">{row.output_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2">{(row.avg_latency_ms / 1000).toFixed(1)} s</td>
                      <td className="px-3 py-2">{usd(row.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="space-y-3">
            <DailySpend days={data.by_day} />
            <details>
              <summary className="cursor-pointer text-ink-2">Show daily figures as a table</summary>
              <table className="mt-2 w-full max-w-sm border-collapse text-right tabular-nums">
                <thead><tr className="text-xs text-ink-2"><th scope="col" className="py-1 text-left font-medium">Day</th><th scope="col" className="py-1 font-medium">Calls</th><th scope="col" className="py-1 font-medium">Cost</th></tr></thead>
                <tbody>{data.by_day.map((d) => <tr key={d.day} className="border-t border-line"><th scope="row" className="py-1 text-left font-normal">{d.day}</th><td className="py-1">{d.calls}</td><td className="py-1">{usd(d.cost_usd)}</td></tr>)}</tbody>
              </table>
            </details>
          </section>
        </div>
      )}
    </main>
  );
}
