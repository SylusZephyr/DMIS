import type { components } from "./api-schema";
// Typed client for the platform API (/api/v2, proxied by next.config rewrites).

export const API = "/api/v2";
const TOKEN_KEY = "dmis_token";

// The API token lives in this browser only (per-viewer convenience). Storage can be
// unavailable (private mode, blocked site data), so every access is guarded.
export function getToken(): string | null {
  try { return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable: token lasts for this page only */ }
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/** A non-2xx API response. `status` lets callers (and the query cache's retry rule) tell 4xx from 5xx. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function check(r: Response) {
  if (r.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("dmis-auth-required"));
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${r.statusText}: ${await r.text()}`);
}

export async function get<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store", ...init, headers: { ...authHeaders(), ...(init?.headers ?? {}) } });
  await check(r);
  return r.json() as Promise<T>;
}

// POSTs that only compute an answer (no stored state changes). Every other successful POST is a mutation
// and marks all cached GET responses stale, so pages that are open refetch and others refetch on next visit.
const READ_ONLY_POST = [/^\/telemetry\//, /^\/imports\/preview/, /^\/analyst\/ask/, /^\/shopping\/recommend/, /^\/launch\/simulate/, /^\/launch\/compare/];
let onMutation: (() => void) | null = null;
/** Registered by the query provider (lib/query.tsx); kept as a callback so this module stays React-free. */
export function setMutationListener(fn: (() => void) | null) { onMutation = fn; }

export async function post<T = unknown>(path: string, body: unknown, opts?: { invalidate?: boolean }): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    body: isForm ? (body as FormData) : JSON.stringify(body),
    headers: isForm ? authHeaders() : { "Content-Type": "application/json", ...authHeaders() },
  });
  await check(r);
  const out = (await r.json()) as T;
  const invalidate = opts?.invalidate ?? !READ_ONLY_POST.some((re) => re.test(path));
  if (invalidate) onMutation?.();
  return out;
}

// generated from the FastAPI response model (GET /markets); regenerate with `npm run gen:api`
export type MarketRow = components["schemas"]["MarketRow"];

export type Segment = {
  segment_id: string; segment_label: string; family_id: string; family_label?: string; products: number; listings: number;
  monthly_revenue: number | null; annual_revenue: number | null; revenue_share: number | null; price_median: number | null;
  price_min: number | null; price_max: number | null; avg_rating: number | null; concentration: string; top_brand: string | null;
  top_brand_share: number | null; opportunity_score: number; opportunity_level: string; coverage: number;
  opportunity_drivers: string; f_demand: number | null; f_growth: number | null; f_customer_pain: number | null;
  f_competition_gap: number | null; f_supplier: number | null; f_difficulty: number | null;
  low_products?: number; middle_products?: number; premium_products?: number;
  low_revenue?: number | null; middle_revenue?: number | null; premium_revenue?: number | null;
};

export type Product = {
  product_id: string; title: string; brand: string | null; product_type: string | null; segment_id: string;
  model_label?: string; image: string | null; price: number | null; price_min?: number | null; price_max?: number | null;
  monthly_sales: number | null; monthly_revenue: number | null; annual_revenue?: number | null; rating: number | null;
  reviews?: number | null; listing_count: number; best_listing?: string; price_tier?: string | null;
  opportunity_score: number | null; market?: string;
  // metrics engine v3 (present once the market is processed with it)
  units_est?: number | null; units_lo?: number | null; units_hi?: number | null; units_floor?: number | null;
  revenue_est?: number | null; revenue_lo?: number | null; revenue_hi?: number | null;
  unit_margin?: number | null; margin_rate?: number | null; is_entrant?: boolean | null;
};

export type GraphNode = { id: string; kind: string; label: string; market?: string | null; props: Record<string, unknown> };
export type GraphEdge = { source: string; target: string; rel: string; props?: Record<string, unknown> };

export type UniverseNode = {
  id: string; label: string; kind: string; market?: string; segment_id?: string; value?: number | null;
  opportunity?: number | null; growth?: number | null; growth_label?: string | null; momentum?: number | null;
  products?: number | null; children?: UniverseNode[];
  value_lo?: number | null; value_hi?: number | null; evidence_grade?: string | null;
};

export type Job = {
  id: string; kind: string; status: "queued" | "running" | "done" | "failed"; market_name: string | null;
  stages: { name: string; status: string; seconds?: number; summary?: unknown }[]; error: string | null;
  report?: IngestionReport | null; created_at: string; finished_at: string | null;
};

export type IngestionReport = {
  raw_rows: number; accepted: number; rejected: number; rejection_reasons: Record<string, number>; adapter: string;
  column_mapping: Record<string, string>; mapping_confidence: Record<string, number>; unmapped_columns: string[]; warnings: string[];
};

// ---- enterprise intelligence layer
export type Reason = { signal: string; ok: boolean; detail: string };
export type TrendRow = {
  scope: string; segment_label?: string | null; trend: string; direction: number | null; confidence: number | null;
  expected_growth_12m: number | null; expected_growth_basis?: string; price_pressure: boolean; periods: number;
  evidence: string[]; signals: Record<string, { value: number; unit: string }>; seasonality: { status: string; note?: string; seasonal?: boolean };
  monthly_revenue?: number | null; products?: number | null;
};
export type Competitor = {
  rank: number; brand: string; position: "Leader" | "Challenger" | "Follower" | "Niche"; share: number; share_basis: string;
  products: number; listings: number | null; monthly_revenue: number | null; median_price: number | null; price_index: number | null;
  avg_rating: number | null; segments: number; top_segment: string | null; new_listings_12m: number;
  weaknesses: string[]; strengths: string[]; opportunities: string[]; complaints: { aspect: string; share_of_reviews: number | null }[];
  changes: { from?: string; to?: string; share_change?: number; price_change?: number; new_listings?: string[]; dropped_listings?: string[];
    rating_change?: number; new_reviews?: number };
  market?: string;
};
export type FitPart = { score: number; evidence: string };
export type Risk = { risk: string; severity: "high" | "medium"; evidence: string };
export type LaunchResult = {
  status: string; market: string; market_alternatives: { market: string; similarity_votes: number }[];
  segment: { segment_id: string; label: string; family: string; monthly_revenue: number | null; products: number | null; price_median: number | null;
    top_brand: string | null; trend: string | null; opportunity_score: number | null; confidence: number | null };
  market_attractiveness: number; fit_score: number; verdict: string; expected_positioning: string | null; price_percentile: number | null;
  main_risk: Risk | null; risks: Risk[]; fit: Record<string, FitPart>; unavailable: string[]; recommended_strategy: string[];
  economics: { price: number; marketplace_fee: number; fulfilment_fee: number; fulfilment_basis: string; unit_cost: number | null;
    unit_cost_basis: string | null; unit_margin: number | null; margin_rate: number | null };
  simulation: { status: string; verdict: string | null; probability_profitable: number | null; profit_p10_p50_p90: number[] | null;
    units_p10_p50_p90: number[] | null } | null;
  suppliers: { supplier_id: string; name: string; country: string | null; match_score: number }[];
  comparables: { product_id: string; title: string; brand: string | null; price: number | null; monthly_sales: number | null; similarity: number }[];
  competitors: { brand: string; monthly_revenue: number | null }[];
};
export type AnalystAnswer = {
  question: string; intent: string; answer: string; items: Record<string, unknown>[]; sources: string[]; followups: string[];
  scope: { markets: string[]; branch: string | null; brand: string | null; basis: string };
  mode: "offline" | "ai"; ai_answer?: string; ai_status?: string; ai_note?: string; model?: string;
};
export type EventRow = { id: string; kind: string; market_name: string | null; subject: string | null; severity: string; source: string;
  payload: Record<string, unknown>; created_at: string };
export type AlertRow = { id: string; status: string; employee_id: string; employee: string | null; event: EventRow; created_at: string };
export type Action = { priority: "high" | "medium" | "low"; market: string | null; action: string; reason: string; action_zh?: string; reason_zh?: string;
  link?: { page: string; market?: string; segment_id?: string; title?: string } | null };
export type Brief = {
  market: string; status: string; industry_branch: string | null;
  market_size: { monthly_revenue: number | null; annual_revenue: number | null; sales_coverage: number | null; note: string };
  products: number | null; opportunity: { level: string; top_score: number | null };
  suggested_development: string | null; trend: { trend?: string; confidence?: number | null; expected_growth_12m?: number | null; evidence?: string[] };
  confidence: { confidence_score: number | null; confidence_level: string } | null;
  competitors: { brand: string; position: string; share: number; weakness: string | null; opportunity: string | null }[];
  suppliers: { supplier_id: string; name: string; country: string | null; segment: string | null; match_score: number }[];
};
export type Focus = { employee_id: string; name: string; new_alerts: number; markets: Brief[]; recommended_actions: Action[];
  categories: { category_id: string; label: string; market: string | null; status: string }[] };
export type HNode = { kind: string; id: string; label: string; products?: number; monthly_revenue?: number | null; opportunity?: number | null;
  confidence?: number | null; basis?: string | null; title?: string; brand?: string | null; price?: number | null; sales?: number | null;
  best?: boolean; children?: HNode[] };

// ---- operations layer (P3)
export type ProjectEvent = { id: string; kind: string; from_stage: string | null; to_stage: string | null; by: string | null;
  text: string | null; data: Record<string, unknown> | null; created_at: string };
export type CommentRow = { id: string; kind: string; rating: number | null; text: string; author: string | null; created_at: string };
export type ProjectRow = { id: string; title: string; market_name: string | null; segment_id: string | null; stage: string;
  status: string; owner_employee_id: string | null; created_by: string | null; updated_at: string;
  idea: Record<string, unknown> | null; tracked_listings: string[] | null;
  prediction: { market_attractiveness?: number; verdict?: string; expected_positioning?: string | null; segment?: string;
    main_risk?: { risk: string; evidence: string } | null; recommended_strategy?: string[];
    units_p10_p50_p90?: number[] | null; revenue_p10_p50_p90?: number[] | null; probability_profitable?: number | null;
    // metrics-v3 launch simulation
    model?: string; attractiveness_basis?: string; market?: string; segment_id?: string;
    risks?: { code: string; severity: string; value: number | string }[];
    economics?: { unit_cost: number | null; unit_cost_basis: string; unit_margin: number | null; fulfilment_fee: number; fulfilment_basis: string };
    gaps?: { feature: string; covered: boolean; lift: number; q_value: number }[];
    profit?: { mean: number; median: number; p10: number; p90: number; p_positive: number } | null;
    break_even_units?: number | null; warnings?: string[] } | null };
export type ProjectDetail = ProjectRow & { history: ProjectEvent[]; comments: CommentRow[]; owner: string | null };
export type Outcome = { status: string; note?: string; launched?: string;
  observations?: { period: string; sales: number | null; revenue: number | null; rating: number | null }[];
  checkpoints?: { months: number; status: string; due?: string; actual_units?: number | null; predicted_units_p50?: number | null;
    within_p10_p90?: boolean; ratio_to_p50?: number | null; actual_range?: (number | null)[]; predicted_range?: (number | null)[] }[] };
export type MessageRow = { id: string; subject: string; body: string; kind: string; read: boolean; emailed: boolean; created_at: string };
export type Summary = { name: string; hours: number; markets: string[]; headline: string[]; items: string[];
  projects: { id: string; title: string; stage: string; status: string }[]; events: number; text: string };

// ---- productization (P3b)
export type OrgSummary = { org_id: string; name: string; plan: string; status: string;
  limits: { markets: number | null; users: number | null; records_per_month: number | null }; features: string[];
  usage_this_month: Record<string, number>; counts: { markets: number; users: number } };
export type TokenRow = { id: string; name: string; revoked: boolean; created_at: string; last_used_at: string | null; expires_at: string | null };
export type Interaction = { id: string; kind: string; product: string | null; market_name: string | null; project_id: string | null;
  unit_price: number | null; currency: string | null; moq: number | null; lead_time_days: number | null; rating: number | null;
  note: string | null; by: string | null; at: string };
export type RankedSupplier = { rank: number; supplier_id: string; name: string; country: string | null; oem: boolean | null; odm: boolean | null;
  certifications: string | null; price_level: string | null; rank_score: number; components: Record<string, number>;
  missing: string[]; evidence: string[]; interactions: number };
export type ScenarioResult = { name: string; price: number; title: string; market_attractiveness: number; verdict: string;
  expected_positioning: string | null; price_percentile: number | null; units_p50: number | null; revenue_p50: number | null;
  profit_p50: number | null; probability_profitable: number | null; unit_margin: number | null; main_risk: Risk | null; risks: number;
  competition: string | null };
export type Comparison = { market: string; segment_id: string; scenarios: ScenarioResult[]; best_by_attractiveness: string | null;
  best_by_profit_p50: string | null; note: string };
