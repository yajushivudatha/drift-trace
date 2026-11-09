import {
  Ban,
  CheckCircle2,
  CornerDownRight,
  Expand,
  FileX2,
  HelpCircle,
  type LucideIcon,
  Repeat2,
  Scale,
  Scissors,
  TrendingUp,
  Waves,
} from "lucide-react";

export type Tone = "d0" | "d1" | "d2" | "d3" | "dx" | "accent";

export interface LabelInfo {
  text: string;
  tone: Tone;
  icon: LucideIcon;
  severity: number;
  meaning: string;
}

export const LABELS: Record<string, LabelInfo> = {
  FAITHFUL: { text: "Faithful", tone: "d0", icon: CheckCircle2, severity: 0, meaning: "The source supports the claim as stated." },
  HEDGE_LOSS: { text: "Hedge lost", tone: "d1", icon: Waves, severity: 1, meaning: "Uncertainty in the source was dropped." },
  CONDITION_DROPPED: { text: "Condition dropped", tone: "d1", icon: Scissors, severity: 1, meaning: "A dose, duration, subgroup or context was removed." },
  SCOPE_GENERALIZATION: { text: "Scope broadened", tone: "d2", icon: Expand, severity: 2, meaning: "The finding was applied to a wider population or setting." },
  CAUSAL_INFLATION: { text: "Causal inflation", tone: "d2", icon: TrendingUp, severity: 2, meaning: "An association was upgraded to causation." },
  MAGNITUDE_DISTORTION: { text: "Magnitude changed", tone: "d2", icon: Scale, severity: 2, meaning: "An effect size or number was changed or overstated." },
  DIRECTION_CONTRADICTION: { text: "Contradicted", tone: "d3", icon: Repeat2, severity: 3, meaning: "The source reports the opposite or a null result." },
  UNSUPPORTED_ATTRIBUTION: { text: "Not in source", tone: "d3", icon: FileX2, severity: 3, meaning: "The claim does not appear anywhere in the source." },
  RETRACTED_SOURCE: { text: "Retracted source", tone: "d3", icon: Ban, severity: 3, meaning: "The source is retracted or has an expression of concern." },
  SECONDHAND_CITATION: { text: "Secondhand", tone: "accent", icon: CornerDownRight, severity: 0, meaning: "The source only cites other work for this. The trace continues there." },
  UNVERIFIABLE: { text: "Could not verify", tone: "dx", icon: HelpCircle, severity: 0, meaning: "The source could not be read well enough to decide." },
};

export const LABEL_ORDER = [
  "RETRACTED_SOURCE",
  "DIRECTION_CONTRADICTION",
  "UNSUPPORTED_ATTRIBUTION",
  "CAUSAL_INFLATION",
  "SCOPE_GENERALIZATION",
  "MAGNITUDE_DISTORTION",
  "HEDGE_LOSS",
  "CONDITION_DROPPED",
  "SECONDHAND_CITATION",
  "FAITHFUL",
  "UNVERIFIABLE",
];

export const labelInfo = (label: string): LabelInfo =>
  LABELS[label] ?? { text: label, tone: "dx", icon: HelpCircle, severity: 0, meaning: "" };

export const toneVar = (tone: Tone) => `var(--${tone})`;

export const toneClasses: Record<Tone, string> = {
  d0: "bg-d0-soft text-d0",
  d1: "bg-d1-soft text-d1",
  d2: "bg-d2-soft text-d2",
  d3: "bg-d3-soft text-d3",
  dx: "bg-dx-soft text-dx",
  accent: "bg-accent-soft text-accent",
};

export function scoreTone(score: number | null): Tone {
  if (score === null) return "dx";
  if (score >= 90) return "d0";
  if (score >= 70) return "d1";
  if (score >= 45) return "d2";
  return "d3";
}

export const FIELD_NAMES: [string, string][] = [
  ["subject", "Subject"],
  ["intervention_or_exposure", "Intervention / exposure"],
  ["outcome", "Outcome"],
  ["population", "Population"],
  ["conditions", "Conditions"],
  ["causal_strength", "Causal strength"],
  ["hedging", "Hedging"],
  ["magnitude", "Magnitude"],
  ["study_type", "Study type"],
];

export const STOP_REASONS: Record<string, string> = {
  origin: "Original finding",
  max_depth: "Stopped at the depth limit",
  no_fulltext: "Full text unavailable",
  unverifiable: "Could not be verified",
  no_citations: "No further citations found",
  unresolved: "Reference could not be matched",
  not_found: "Claim not found here",
};
