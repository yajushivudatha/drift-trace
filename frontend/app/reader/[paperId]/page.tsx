"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { Suspense, use } from "react";

import { ErrorBox, FulltextBadge, PaperLine, Skeleton } from "@/components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { api, pdfUrl, type SentenceRow } from "@/lib/api";
import { cn } from "@/lib/utils";

const PdfViewer = dynamic(() => import("@/components/pdf-viewer"), { ssr: false, loading: () => <Skeleton className="h-96" /> });

const squash = (text: string) => text.toLowerCase().replace(/[\s\-‐‑­]/g, "");

function Reader({ paperId }: { paperId: string }) {
  const params = useSearchParams();
  const q = params.get("q");
  const { data, isLoading, error } = useQuery({ queryKey: ["sentences", paperId, "all"], queryFn: () => api.sentences(paperId, false) });
  const target = React.useRef<HTMLSpanElement>(null);

  const matches = React.useMemo(() => {
    if (!q || !data) return new Set<number>();
    const form = squash(q);
    return new Set(data.sentences.filter((s) => { const own = squash(s.text); return own.length > 12 && (form.includes(own) || own.includes(form)); }).map((s) => s.index));
  }, [q, data]);

  React.useEffect(() => {
    target.current?.scrollIntoView({ block: "center" });
  }, [matches]);

  const sections = React.useMemo(() => {
    const groups: { title: string; sentences: SentenceRow[] }[] = [];
    data?.sentences.forEach((sentence) => {
      if (groups[groups.length - 1]?.title !== sentence.section) groups.push({ title: sentence.section, sentences: [] });
      groups[groups.length - 1].sentences.push(sentence);
    });
    return groups;
  }, [data]);

  if (isLoading) return <main className="mx-auto max-w-[720px] space-y-3 p-6"><p className="text-ink-2">Loading the paper. A first read can take a minute.</p><Skeleton className="h-96" /></main>;
  if (error || !data) return <main className="p-4"><ErrorBox message={(error as Error)?.message ?? "This paper could not be loaded."} /></main>;
  const { paper } = data;
  let firstMatch = true;

  const textView = (
    <article className="quote text-[16px] leading-[1.7]">
      {sections.length === 0 && (
        <>
          <p className="mb-3 font-sans text-ink-2">{paper.fulltext_note ?? "No text is available for this paper."}</p>
          {paper.abstract && <p>{paper.abstract}</p>}
        </>
      )}
      {sections.map((section, index) => (
        <section key={index} className="mb-6">
          {section.title && <h2 className="mb-1.5 font-sans text-[15px] font-semibold">{section.title}</h2>}
          <p>
            {section.sentences.map((sentence) => {
              const hit = matches.has(sentence.index);
              const ref = hit && firstMatch ? target : undefined;
              if (hit) firstMatch = false;
              return <span key={sentence.index} ref={ref} className={cn(hit && "rounded-[2px] bg-accent-soft")}>{sentence.text} </span>;
            })}
          </p>
        </section>
      ))}
    </article>
  );

  return (
    <main className="mx-auto max-w-[760px] px-4 py-8">
      <div className="mb-4">
        <PaperLine paper={paper} />
        <p className="mt-1 flex items-center gap-3">
          <FulltextBadge status={paper.fulltext_status} />
          {paper.doi && !paper.is_sample && <a className="inline-flex items-center gap-0.5 text-xs text-accent underline underline-offset-2" href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">Publisher page <ExternalLink className="size-3" aria-hidden /></a>}
        </p>
      </div>
      {paper.has_pdf ? (
        <Tabs defaultValue="text">
          <TabsList className="mb-4"><TabsTrigger value="text">Text</TabsTrigger><TabsTrigger value="pdf">PDF</TabsTrigger></TabsList>
          <TabsContent value="text">{textView}</TabsContent>
          <TabsContent value="pdf"><PdfViewer url={pdfUrl(paper.id)} height="78vh" /></TabsContent>
        </Tabs>
      ) : textView}
    </main>
  );
}

export default function ReaderPage({ params }: { params: Promise<{ paperId: string }> }) {
  const { paperId } = use(params);
  return <Suspense><Reader paperId={paperId} /></Suspense>;
}
