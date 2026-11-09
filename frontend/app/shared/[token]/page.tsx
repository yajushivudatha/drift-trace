"use client";

import { useQuery } from "@tanstack/react-query";
import { use } from "react";

import { ErrorBox, Skeleton } from "@/components/common";
import { TraceWorkspace } from "@/components/trace-workspace";
import { api } from "@/lib/api";

export default function SharedPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const { data, isLoading, error } = useQuery({ queryKey: ["shared", token], queryFn: () => api.shared(token) });
  if (isLoading) return <div className="p-4"><Skeleton className="h-96" /></div>;
  if (error || !data) return <div className="p-4"><ErrorBox message={(error as Error)?.message ?? "This share link is not valid."} /></div>;
  return <TraceWorkspace trace={data} readOnly />;
}
