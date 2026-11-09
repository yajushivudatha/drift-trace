export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type FulltextStatus = "FULL" | "ABSTRACT_ONLY" | "NONE" | null;

export interface PaperSummary {
  id: string;
  doi: string | null;
  title: string | null;
  authors: string[];
  year: number | null;
  venue: string | null;
  short_cite: string;
  fulltext_status: FulltextStatus;
  fulltext_source: string | null;
  fulltext_note: string | null;
  is_retracted: boolean;
  retraction_note: string | null;
  is_oa: boolean;
  work_type: string | null;
  related_versions: { relation: string; doi: string }[];
  parse_quality: string | null;
  has_pdf: boolean;
  is_sample: boolean;
}

export interface PaperDetail extends PaperSummary {
  abstract: string | null;
}

export interface FieldRow {
  claimed: string;
  source: string;
  match: "yes" | "partial" | "no";
}

export interface SourceQuote {
  text: string;
  section: string | null;
  page: number | null;
}

export interface HopFeedback {
  agrees: boolean;
  user_label: string;
  note: string | null;
}

export interface Hop {
  id: string;
  from: string;
  to: string;
  marker: string | null;
  claimed_quote: string;
  claimed_location: { section?: string | null; page?: number | null };
  source_quotes: SourceQuote[];
  primary_label: string;
  secondary_labels: string[];
  severity: number;
  confidence: "high" | "medium" | "low";
  confidence_reason: string;
  explanation: string;
  field_comparison: Record<string, FieldRow>;
  highlights: {
    source_hedges_removed?: string[];
    source_conditions_dropped?: string[];
    claimed_causal_terms?: string[];
    source_causal_terms?: string[];
  };
  quotes_verified: boolean;
  source_status: FulltextStatus;
  is_self_citation: boolean;
  is_cycle: boolean;
  gold_label: string | null;
  feedback: HopFeedback | null;
}

export interface TraceNode {
  id: string;
  depth: number;
  is_origin: boolean;
  stop_reason: string | null;
  label: string | null;
  claim_text: string | null;
  claim_frame: Record<string, unknown> | null;
  times_cited: number;
  paper: PaperSummary | null;
}

export interface Trace {
  id: string;
  input_type: string;
  claim: string;
  status: "queued" | "running" | "cancelling" | "done" | "failed" | "cancelled";
  error: string | null;
  settings: { max_depth?: number; max_branches?: number; mode?: string; doi?: string };
  fidelity_score: number | null;
  summary: string | null;
  is_sample: boolean;
  share_token: string | null;
  created_at: string;
  finished_at: string | null;
  score_formula: string;
  label_counts: Record<string, number>;
  start_paper: PaperSummary | null;
  llm_calls: number;
  cost_usd: number;
  worst_path: string[];
  nodes: TraceNode[];
  hops: Hop[];
}

export interface TraceListItem {
  id: string;
  claim: string;
  status: Trace["status"];
  fidelity_score: number | null;
  created_at: string;
  label_counts: Record<string, number>;
  is_sample: boolean;
  paper: PaperSummary | null;
}

export interface SentenceRow {
  index: number;
  text: string;
  section: string;
  kind: string;
  page: number | null;
  citations: { marker: string; ref_keys: string[] }[];
}

export interface ReviewItem extends Hop {
  trace_id: string;
  gold_note: string | null;
  citing: PaperSummary | null;
  cited: PaperSummary | null;
  cited_label: string | null;
}

export interface CostStats {
  total_cost_usd: number;
  total_calls: number;
  cache_hits: number;
  input_tokens: number;
  output_tokens: number;
  avg_cost_per_trace: number;
  pricing_configured: boolean;
  by_purpose: {
    purpose: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    avg_latency_ms: number;
    cache_hits: number;
  }[];
  by_day: { day: string; cost_usd: number; calls: number }[];
}

export interface Health {
  status: string;
  database: boolean;
  redis: boolean;
  grobid: boolean;
  llm_configured: boolean;
  contact_email_set: boolean;
}

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError("The DriftTrace server is not responding. Check that the backend is running.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(typeof detail === "string" ? detail : "The request could not be completed. Check the fields and try again.");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export interface TraceRequest {
  input_type: "doi" | "upload" | "free_text";
  claim: string;
  doi?: string;
  paper_id?: string;
  settings: { max_depth: number; max_branches: number; mode: "fast" | "thorough" };
}

export const api = {
  health: () => request<Health>("/health"),
  resolve: (value: string) => post<PaperDetail>("/papers/resolve", { doi: value }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<PaperDetail>("/papers/upload", { method: "POST", body: form });
  },
  paper: (id: string) => request<PaperDetail>(`/papers/${id}`),
  sentences: (id: string, withCitations: boolean) =>
    request<{ paper: PaperDetail; sentences: SentenceRow[] }>(`/papers/${id}/sentences?with_citations=${withCitations}`),
  createTrace: (body: TraceRequest) => post<{ id: string }>("/traces", body),
  trace: (id: string) => request<Trace>(`/traces/${id}`),
  shared: (token: string) => request<Trace>(`/shared/${token}`),
  cancel: (id: string) => post<{ status: string }>(`/traces/${id}/cancel`),
  remove: (id: string) => request<void>(`/traces/${id}`, { method: "DELETE" }),
  traces: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    return request<{ total: number; page: number; per_page: number; items: TraceListItem[] }>(`/traces?${query}`);
  },
  feedback: (hopId: string, body: { agrees: boolean; user_label?: string; note?: string }) =>
    post<HopFeedback>(`/hops/${hopId}/feedback`, body),
  share: (id: string) => post<{ share_token: string }>(`/traces/${id}/share`),
  reviewQueue: () => request<{ items: ReviewItem[] }>("/review/queue"),
  setGold: (hopId: string, body: { gold_label: string; note?: string }) => post(`/hops/${hopId}/gold`, body),
  costs: () => request<CostStats>("/stats/cost"),
};

export const exportUrl = (id: string, format: "md" | "pdf" | "json") => `${API_URL}/traces/${id}/export?format=${format}`;
export const pdfUrl = (paperId: string) => `${API_URL}/papers/${paperId}/pdf`;
export const reviewExportUrl = `${API_URL}/review/export.jsonl`;
