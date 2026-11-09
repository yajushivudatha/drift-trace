"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, FileUp, ListChecks, Loader2, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import * as React from "react";

import { ErrorBox, FulltextBadge, PaperLine, Skeleton } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Slider, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { api, pdfUrl, type PaperDetail, type SentenceRow, type TraceRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

const PdfViewer = dynamic(() => import("@/components/pdf-viewer"), { ssr: false, loading: () => <Skeleton className="h-96" /> });

function PaperChip({ paper, onClear }: { paper: PaperDetail; onClear: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-[5px] border border-line bg-bg px-3 py-2">
      <div className="min-w-0 flex-1">
        <PaperLine paper={paper} />
        <p className="mt-1 flex items-center gap-2 text-2xs text-ink-2">
          {paper.fulltext_status ? <FulltextBadge status={paper.fulltext_status} /> : <span>{paper.is_oa ? "Open access" : "May be paywalled"}</span>}
          {paper.is_retracted && <span className="font-semibold text-d3">Retracted</span>}
        </p>
      </div>
      <Button variant="ghost" size="icon" onClick={onClear} aria-label="Clear this paper"><X /></Button>
    </div>
  );
}

function usePaperField() {
  const [value, setValue] = React.useState("");
  const [paper, setPaper] = React.useState<PaperDetail | null>(null);
  const resolve = useMutation({ mutationFn: api.resolve, onSuccess: setPaper });
  const onBlur = () => {
    if (value.trim() && value.trim() !== paper?.doi) resolve.mutate(value.trim());
  };
  const clear = () => {
    setPaper(null);
    setValue("");
    resolve.reset();
  };
  return { value, setValue, paper, onBlur, clear, pending: resolve.isPending, error: resolve.error as Error | null };
}

function DoiField({ field, id }: { field: ReturnType<typeof usePaperField>; id: string }) {
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block font-medium">Paper DOI or URL</label>
      {field.paper ? (
        <PaperChip paper={field.paper} onClear={field.clear} />
      ) : (
        <div className="relative">
          <input id={id} className="field" placeholder="10.1371/journal.pmed.0020124" value={field.value} onChange={(e) => field.setValue(e.target.value)} onBlur={field.onBlur} autoComplete="off" spellCheck={false} />
          {field.pending && <Loader2 className="absolute right-3 top-2.5 size-4 animate-spin text-ink-3 motion-reduce:animate-none" aria-label="Looking up paper" />}
        </div>
      )}
      {field.error && <ErrorBox message={field.error.message} />}
    </div>
  );
}

function SentencePicker({ paper, onPick, onClose }: { paper: PaperDetail; onPick: (text: string) => void; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["sentences", paper.id], queryFn: () => api.sentences(paper.id, true) });
  React.useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const groups = React.useMemo(() => {
    const map = new Map<string, SentenceRow[]>();
    data?.sentences.forEach((s) => map.set(s.section || "Text", [...(map.get(s.section || "Text") ?? []), s]));
    return [...map.entries()];
  }, [data]);
  return (
    <div role="dialog" aria-modal="true" aria-label="Pick a sentence from the paper" className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-[8px] bg-surface" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="font-semibold">Pick a sentence that cites a source</h2>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close"><X /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {isLoading && <div className="space-y-2"><p className="text-ink-2">Fetching and reading the paper. This can take a minute.</p><Skeleton className="h-16" /><Skeleton className="h-16" /></div>}
          {error && <ErrorBox message={(error as Error).message} />}
          {data && !data.sentences.length && <p className="text-ink-2">{data.paper.fulltext_status === "FULL" ? "No sentences with citations were found in this paper." : "The full text of this paper is not openly available. Paste the sentence above, or upload the PDF."}</p>}
          {groups.map(([section, sentences]) => (
            <section key={section} className="mb-3">
              <h3 className="sticky top-0 bg-surface py-1 text-xs font-semibold text-ink-2">{section}</h3>
              {sentences.map((sentence) => (
                <button key={sentence.index} onClick={() => onPick(sentence.text)} className="quote block w-full rounded-[4px] px-2 py-1.5 text-left text-[14px] leading-[1.5] hover:bg-accent-soft">
                  {sentence.text}
                </button>
              ))}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

export function NewTraceCard({ llmReady }: { llmReady: boolean }) {
  const router = useRouter();
  const [tab, setTab] = React.useState<"doi" | "upload" | "free_text">("doi");
  const doi = usePaperField();
  const freeDoi = usePaperField();
  const [claim, setClaim] = React.useState("");
  const [uploadClaim, setUploadClaim] = React.useState("");
  const [freeClaim, setFreeClaim] = React.useState("");
  const [upload, setUpload] = React.useState<PaperDetail | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [picking, setPicking] = React.useState(false);
  const [advanced, setAdvanced] = React.useState(false);
  const [depth, setDepth] = React.useState(4);
  const [branches, setBranches] = React.useState(3);
  const [mode, setMode] = React.useState<"fast" | "thorough">("fast");
  const fileRef = React.useRef<HTMLInputElement>(null);

  const uploader = useMutation({ mutationFn: api.upload, onSuccess: setUpload });
  const start = useMutation({ mutationFn: api.createTrace, onSuccess: ({ id }) => router.push(`/traces/${id}`) });

  const take = (files: FileList | null) => {
    if (files?.[0]) uploader.mutate(files[0]);
  };

  const body: TraceRequest | null = React.useMemo(() => {
    const settings = { max_depth: depth, max_branches: branches, mode };
    if (tab === "doi" && doi.value.trim() && claim.trim().length >= 8) return { input_type: "doi", doi: doi.paper?.doi ?? doi.value.trim(), claim, settings };
    if (tab === "upload" && upload && uploadClaim.trim().length >= 8) return { input_type: "upload", paper_id: upload.id, claim: uploadClaim, settings };
    if (tab === "free_text" && freeDoi.value.trim() && freeClaim.trim().length >= 8) return { input_type: "free_text", doi: freeDoi.paper?.doi ?? freeDoi.value.trim(), claim: freeClaim, settings };
    return null;
  }, [tab, doi.value, doi.paper, claim, upload, uploadClaim, freeDoi.value, freeDoi.paper, freeClaim, depth, branches, mode]);

  const canPick = doi.paper && (doi.paper.is_oa || doi.paper.fulltext_status === "FULL");

  return (
    <div className="rounded-[8px] border border-line bg-surface">
      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList className="px-2">
          <TabsTrigger value="doi">DOI + sentence</TabsTrigger>
          <TabsTrigger value="upload">Upload PDF</TabsTrigger>
          <TabsTrigger value="free_text">Free-text claim</TabsTrigger>
        </TabsList>

        <TabsContent value="doi" className="space-y-3 p-4">
          <DoiField field={doi} id="doi-input" />
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label htmlFor="claim-input" className="font-medium">Sentence containing the claim</label>
              {canPick && <Button size="sm" variant="ghost" onClick={() => setPicking(true)}><ListChecks /> Pick sentence from paper</Button>}
            </div>
            <textarea id="claim-input" className="field quote text-[15px] leading-[1.5]" rows={3} placeholder="Paste the sentence, with its citation, as it appears in the paper." value={claim} onChange={(e) => setClaim(e.target.value)} />
          </div>
        </TabsContent>

        <TabsContent value="upload" className="space-y-3 p-4">
          {upload ? (
            <>
              <PaperChip paper={upload} onClear={() => { setUpload(null); setUploadClaim(""); uploader.reset(); }} />
              {upload.has_pdf && <PdfViewer url={pdfUrl(upload.id)} onSelectText={setUploadClaim} height="46vh" />}
              <div className="space-y-1.5">
                <label htmlFor="upload-claim" className="font-medium">Claim sentence</label>
                <textarea id="upload-claim" className="field quote text-[15px] leading-[1.5]" rows={3} placeholder="Highlight the sentence in the PDF above, or paste it here." value={uploadClaim} onChange={(e) => setUploadClaim(e.target.value)} />
              </div>
            </>
          ) : (
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => { e.preventDefault(); setDragging(false); take(e.dataTransfer.files); }}
              className={cn("flex h-44 w-full flex-col items-center justify-center gap-2 rounded-[6px] border border-dashed border-line text-ink-2 hover:border-accent", dragging && "border-accent bg-accent-soft")}
            >
              {uploader.isPending ? <><Loader2 className="size-5 animate-spin motion-reduce:animate-none" aria-hidden /> Reading the PDF. This can take a minute.</> : <><FileUp className="size-5" aria-hidden /> Drop a PDF here, or click to choose one</>}
            </button>
          )}
          <input ref={fileRef} type="file" accept="application/pdf" className="hidden" aria-label="Choose a PDF" onChange={(e) => take(e.target.files)} />
          {uploader.isError && <ErrorBox message={(uploader.error as Error).message} />}
        </TabsContent>

        <TabsContent value="free_text" className="space-y-3 p-4">
          <div className="space-y-1.5">
            <label htmlFor="free-claim" className="font-medium">The claim</label>
            <textarea id="free-claim" className="field quote text-[15px] leading-[1.5]" rows={3} placeholder="Paste the claim as it appeared in a news story, report or post." value={freeClaim} onChange={(e) => setFreeClaim(e.target.value)} />
          </div>
          <DoiField field={freeDoi} id="free-doi" />
        </TabsContent>
      </Tabs>

      <div className="border-t border-line px-4 py-3">
        <button className="flex items-center gap-1 font-medium text-ink-2 hover:text-ink" aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)}>
          <ChevronDown className={cn("size-4 transition-transform motion-reduce:transition-none", !advanced && "-rotate-90")} aria-hidden /> Advanced
        </button>
        {advanced && (
          <div className="mt-3 grid gap-x-8 gap-y-3 sm:grid-cols-3">
            <div>
              <p className="mb-1 flex justify-between"><span>Maximum depth</span><span className="font-semibold tabular-nums">{depth} hops</span></p>
              <Slider value={depth} onChange={setDepth} min={1} max={8} label="Maximum depth" />
            </div>
            <div>
              <p className="mb-1 flex justify-between"><span>Branches per hop</span><span className="font-semibold tabular-nums">{branches}</span></p>
              <Slider value={branches} onChange={setBranches} min={1} max={10} label="Branches per hop" />
            </div>
            <div>
              <p className="mb-1">Mode</p>
              <div className="inline-flex rounded-[5px] border border-line p-0.5" role="radiogroup" aria-label="Mode">
                {(["fast", "thorough"] as const).map((option) => (
                  <button key={option} role="radio" aria-checked={mode === option} onClick={() => setMode(option)} className={cn("rounded-[3px] px-3 py-0.5 capitalize", mode === option ? "bg-ink text-bg" : "text-ink-2")}>
                    {option}
                  </button>
                ))}
              </div>
              <p className="mt-1 text-2xs text-ink-3">{mode === "fast" ? "Abstracts and readily available open-access text." : "Tries every full-text source. Slower."}</p>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-3 border-t border-line px-4 py-3">
        {start.isError && <div className="min-w-0 flex-1"><ErrorBox message={(start.error as Error).message} /></div>}
        <Button variant="primary" size="lg" disabled={!body || start.isPending || !llmReady} onClick={() => body && start.mutate(body)}>
          {start.isPending ? "Starting" : "Start trace"}
        </Button>
      </div>

      {picking && doi.paper && <SentencePicker paper={doi.paper} onClose={() => setPicking(false)} onPick={(text) => { setClaim(text); setPicking(false); }} />}
    </div>
  );
}
