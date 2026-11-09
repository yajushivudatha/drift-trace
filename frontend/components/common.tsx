"use client";

import { AlertTriangle, BadgeCheck, FileQuestion, FileText, FlaskConical } from "lucide-react";
import * as React from "react";

import { Tooltip } from "@/components/ui/primitives";
import type { FulltextStatus, PaperSummary } from "@/lib/api";
import { labelInfo, scoreTone, toneClasses, toneVar } from "@/lib/labels";
import { cn } from "@/lib/utils";

export function LabelChip({ label, size = "md", className }: { label: string; size?: "sm" | "md"; className?: string }) {
  const info = labelInfo(label);
  const Icon = info.icon;
  return (
    <Tooltip content={info.meaning}>
      <span
        tabIndex={0}
        className={cn(
          "inline-flex items-center gap-1 whitespace-nowrap rounded-[3px] font-medium",
          size === "sm" ? "px-1.5 py-px text-2xs" : "px-2 py-0.5 text-xs",
          toneClasses[info.tone],
          className,
        )}
      >
        <Icon className={size === "sm" ? "size-3" : "size-3.5"} aria-hidden />
        {info.text}
      </span>
    </Tooltip>
  );
}

export function ScorePill({ score }: { score: number | null }) {
  const tone = scoreTone(score);
  return (
    <span className={cn("inline-flex min-w-11 justify-center rounded-[3px] px-1.5 py-0.5 text-[13px] font-semibold tabular-nums", toneClasses[tone])}>
      {score ?? "n/a"}
      <span className="sr-only"> fidelity out of 100</span>
    </span>
  );
}

export function ScoreBadge({ score, formula }: { score: number | null; formula: string }) {
  const tone = scoreTone(score);
  return (
    <Tooltip content={<span className="block">{formula}</span>}>
      <button className="group flex shrink-0 items-center gap-3 text-left" aria-label={`Fidelity score ${score ?? "not scored"}. Show the formula.`}>
        <span className="quote text-[44px] font-semibold leading-none tabular-nums" style={{ color: toneVar(tone) }}>
          {score ?? "—"}
        </span>
        <span className="flex flex-col">
          <span className="text-xs font-medium text-ink">{score === null ? "Not scored" : "Fidelity of 100"}</span>
          <span className="text-2xs text-ink-3 underline decoration-dotted underline-offset-2 group-hover:text-ink">How it’s scored</span>
        </span>
      </button>
    </Tooltip>
  );
}

const STATUS: Record<string, { text: string; icon: typeof FileText }> = {
  FULL: { text: "Full text", icon: FileText },
  ABSTRACT_ONLY: { text: "Abstract only", icon: FileQuestion },
  NONE: { text: "No text", icon: AlertTriangle },
};

export function FulltextBadge({ status, compact }: { status: FulltextStatus; compact?: boolean }) {
  const info = STATUS[status ?? "NONE"];
  if (!status && compact) return null;
  const Icon = info.icon;
  return (
    <span className="inline-flex items-center gap-1 text-2xs text-ink-2" title={info.text}>
      <Icon className="size-3" aria-hidden />
      {compact ? <span className="sr-only">{info.text}</span> : info.text}
    </span>
  );
}

export function PaperLine({ paper }: { paper: PaperSummary }) {
  return (
    <div className="min-w-0">
      <p className="font-medium leading-snug">{paper.title ?? "Untitled paper"}</p>
      <p className="text-xs text-ink-2">
        {[paper.authors.slice(0, 3).join(", ") + (paper.authors.length > 3 ? " et al." : ""), paper.year, paper.venue].filter(Boolean).join(", ")}
      </p>
    </div>
  );
}

export function SampleTag() {
  return (
    <Tooltip content="This trace uses invented papers to show how results look. It is not a real analysis.">
      <span tabIndex={0} className="inline-flex items-center gap-1 rounded-[3px] bg-accent-soft px-1.5 py-px text-2xs font-medium text-accent">
        <FlaskConical className="size-3" aria-hidden /> Sample data
      </span>
    </Tooltip>
  );
}

export function VerifiedTag() {
  return (
    <Tooltip content="Each quotation was matched against the stored text of the paper.">
      <span tabIndex={0} className="inline-flex items-center gap-1 text-2xs text-d0">
        <BadgeCheck className="size-3.5" aria-hidden /> Quotes verified
      </span>
    </Tooltip>
  );
}

export function ErrorBox({ message, action }: { message: string; action?: React.ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-[5px] bg-d3-soft px-3 py-2 text-d3">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p>{message}</p>
        {action}
      </div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-[4px] bg-sunken", className)} />;
}
