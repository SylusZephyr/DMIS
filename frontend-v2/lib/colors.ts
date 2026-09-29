import { scaleSequential } from "d3-scale";
import { interpolateRgb } from "d3-interpolate";

// Magnitude colours are single-hue sequential (dim navy -> bright cyan) so
// "more" always reads as "brighter" on the dark canvas. Categorical entity
// colours use a fixed order and are never assigned by rank.
const LOW = "#12324a";
const HIGH = "#6fe8ff";
const seq = scaleSequential(interpolateRgb(LOW, HIGH)).domain([30, 75]).clamp(true);

export const opportunityColor = (score?: number | null) => (score == null ? "#2a3b55" : seq(score));
export const OPPORTUNITY_RAMP = [30, 40, 50, 60, 70, 75].map((v) => ({ v, c: seq(v) }));

export const KIND_COLORS: Record<string, string> = {
  Industry: "#e6eefb", Category: "#38d6ff", ProductFamily: "#8b7bff", Segment: "#f5b942", Product: "#2ecc8f",
  Listing: "#62789a", Brand: "#ff7eb6", Seller: "#c08bff", Supplier: "#ff9f5a", Country: "#7ad3a8",
  CustomerProblem: "#ff5f6d",
};

// Trend and confidence are status colours (fixed meaning), not magnitudes.
export const TREND_COLORS: Record<string, string> = {
  Growing: "var(--good)", Emerging: "#7fe0b6", Stable: "var(--ink-2)", Mature: "var(--warn)", Declining: "var(--bad)",
};
export const trendColor = (t?: string | null) => TREND_COLORS[t ?? ""] ?? "var(--ink-3)";
export const confidenceColor = (level?: string | null) =>
  ({ high: "var(--good)", medium: "var(--warn)", low: "var(--bad)" })[level ?? ""] ?? "var(--ink-3)";
export const severityColor = (s?: string | null) =>
  ({ important: "var(--bad)", notice: "var(--warn)", info: "var(--ink-3)" })[s ?? ""] ?? "var(--ink-3)";

export const levelColor = (level?: string | null) =>
  ({ "Very high": "var(--good)", High: "#7fe0b6", Moderate: "var(--warn)", Low: "var(--ink-3)" })[level ?? ""] ?? "var(--ink-3)";
