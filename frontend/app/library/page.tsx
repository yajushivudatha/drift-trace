"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ErrorBox, LabelChip, SampleTag, ScorePill, Skeleton } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { LABEL_ORDER, LABELS } from "@/lib/labels";
import { formatDate } from "@/lib/utils";

const PER_PAGE = 20;

export default function LibraryPage() {
  const client = useQueryClient();
  const [search, setSearch] = React.useState("");
  const [query, setQuery] = React.useState("");
  const [label, setLabel] = React.useState("all");
  const [sort, setSort] = React.useState<"date" | "score">("date");
  const [order, setOrder] = React.useState<"asc" | "desc">("desc");
  const [page, setPage] = React.useState(1);
  const [confirming, setConfirming] = React.useState<string | null>(null);

  React.useEffect(() => {
    const timer = setTimeout(() => { setQuery(search); setPage(1); }, 250);
    return () => clearTimeout(timer);
  }, [search]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["traces", query, label, sort, order, page],
    queryFn: () => api.traces({ q: query, label: label === "all" ? undefined : label, sort, order, page, per_page: PER_PAGE }),
    placeholderData: keepPreviousData,
  });
  const remove = useMutation({
    mutationFn: api.remove,
    onSuccess: () => { setConfirming(null); client.invalidateQueries({ queryKey: ["traces"] }); },
  });

  const header = (key: "date" | "score", text: string) => (
    <button
      className="inline-flex items-center gap-1 font-medium hover:text-ink"
      onClick={() => { if (sort === key) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(key); setOrder("desc"); } setPage(1); }}
    >
      {text}
      {sort === key && (order === "asc" ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />)}
    </button>
  );
  const pages = data ? Math.max(1, Math.ceil(data.total / PER_PAGE)) : 1;

  return (
    <main className="mx-auto max-w-[1080px] px-4 py-8">
      <h1 className="mb-4 text-[20px] font-semibold">Library</h1>
      <div className="mb-3 flex flex-wrap gap-2">
        <input className="field max-w-sm flex-1 basis-56" placeholder="Search claims" aria-label="Search claims" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Select label="Filter by drift type" className="w-52" value={label} onChange={(v) => { setLabel(v); setPage(1); }} options={[{ value: "all", label: "All drift types" }, ...LABEL_ORDER.map((value) => ({ value, label: LABELS[value].text }))]} />
      </div>
      {error && <ErrorBox message={(error as Error).message} />}
      {remove.isError && <ErrorBox message={(remove.error as Error).message} />}
      {isLoading && <Skeleton className="h-64" />}
      {data && (
        <div className="overflow-x-auto rounded-[6px] border border-line bg-surface">
          <table className="w-full min-w-[720px] border-collapse">
            <thead>
              <tr className="border-b border-line text-left text-xs text-ink-2">
                <th scope="col" className="w-20 px-3 py-2" aria-sort={sort === "score" ? (order === "asc" ? "ascending" : "descending") : "none"}>{header("score", "Score")}</th>
                <th scope="col" className="px-3 py-2 font-medium">Claim</th>
                <th scope="col" className="px-3 py-2 font-medium">Drift found</th>
                <th scope="col" className="w-28 px-3 py-2" aria-sort={sort === "date" ? (order === "asc" ? "ascending" : "descending") : "none"}>{header("date", "Date")}</th>
                <th scope="col" className="w-24 px-3 py-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.items.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-8 text-center text-ink-2">{query || label !== "all" ? "No traces match these filters." : <>No traces yet. <Link className="text-accent underline underline-offset-2" href="/">Start one</Link>.</>}</td></tr>
              )}
              {data.items.map((trace) => (
                <tr key={trace.id} className="border-b border-line last:border-0 hover:bg-sunken/50">
                  <td className="px-3 py-2"><ScorePill score={trace.fidelity_score} /></td>
                  <td className="max-w-md px-3 py-2">
                    <Link href={`/traces/${trace.id}`} className="quote line-clamp-2 text-[14px] leading-snug hover:underline">{trace.claim}</Link>
                    <span className="mt-0.5 flex items-center gap-2 text-xs text-ink-3">
                      {trace.is_sample && <SampleTag />}
                      {trace.status !== "done" && <span className="capitalize">{trace.status}</span>}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {LABEL_ORDER.filter((l) => trace.label_counts[l] && !["FAITHFUL", "SECONDHAND_CITATION"].includes(l)).slice(0, 3).map((l) => <LabelChip key={l} label={l} size="sm" />)}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs tabular-nums text-ink-2">{formatDate(trace.created_at)}</td>
                  <td className="px-3 py-2 text-right">
                    {confirming === trace.id ? (
                      <span className="inline-flex gap-1">
                        <Button size="sm" className="border-d3 text-d3" onClick={() => remove.mutate(trace.id)} disabled={remove.isPending}>Delete</Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>Keep</Button>
                      </span>
                    ) : (
                      <Button size="icon" variant="ghost" onClick={() => setConfirming(trace.id)} aria-label="Delete this trace"><Trash2 /></Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && pages > 1 && (
        <nav className="mt-3 flex items-center justify-end gap-2" aria-label="Pages">
          <Button size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <span className="text-xs tabular-nums text-ink-2">Page {page} of {pages}</span>
          <Button size="sm" disabled={page === pages} onClick={() => setPage(page + 1)}>Next</Button>
        </nav>
      )}
    </main>
  );
}
