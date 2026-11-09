"use client";

import { useQuery } from "@tanstack/react-query";
import { use } from "react";

import { ErrorBox, Skeleton } from "@/components/common";
import { TraceProgress } from "@/components/trace-progress";
import { TraceWorkspace } from "@/components/trace-workspace";
import { api } from "@/lib/api";

export default function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, isLoading, error } = useQuery({ queryKey: ["trace", id], queryFn: () => api.trace(id) });
  if (isLoading) return <div className="space-y-3 p-4"><Skeleton className="h-16" /><Skeleton className="h-96" /></div>;
  if (error || !data) return <div className="p-4"><ErrorBox message={(error as Error)?.message ?? "This trace could not be found."} /></div>;
  return data.status === "done" ? <TraceWorkspace trace={data} /> : <TraceProgress trace={data} />;
}
