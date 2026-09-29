// Canvas charts cannot read CSS custom properties, so chart options are resolved against the current
// theme before they reach ECharts:
//   * "var(--token)" strings become the token's computed value (preferred way to colour chart text);
//   * the dark-theme literals that chart options were written with (ink / muted ink / panel / line hexes)
//     are mapped to their tokens, so older options follow the theme without edits;
//   * in the light theme the sequential "more = brighter" ramp (lib/viz SEQ) is reversed to
//     "more = darker", which is what reads as "more" on a light surface.
import { SEQ } from "./viz";

export type ChartTheme = "light" | "dark";
export const TOKEN_NAMES = ["--ink", "--ink-2", "--ink-3", "--panel", "--panel-2", "--line", "--chart-split", "--accent", "--good", "--warn", "--bad"] as const;
export type Tokens = Partial<Record<(typeof TOKEN_NAMES)[number], string>>;

const LEGACY: Record<string, (typeof TOKEN_NAMES)[number]> = {
  "#e6eefb": "--ink", "#9fb3cf": "--ink-2", "#8ea0bb": "--ink-2", "#c9d8ee": "--ink-2", "#62789a": "--ink-3",
  "#0a1220": "--panel", "#0f1a2d": "--panel-2", "#1b2a44": "--line", "rgba(56,214,255,0.06)": "--chart-split",
};
const SEQ_REVERSED = new Map(SEQ.map((c, i) => [c.toLowerCase(), SEQ[SEQ.length - 1 - i]]));

export function resolveColor(v: string, tokens: Tokens, theme: ChartTheme): string {
  const m = /^var\((--[a-z0-9-]+)\)$/.exec(v.trim());
  if (m) return tokens[m[1] as keyof Tokens] || v;
  const key = v.toLowerCase().replace(/\s+/g, "");
  const tok = LEGACY[key];
  if (tok && tokens[tok]) return tokens[tok] as string;
  if (theme === "light" && SEQ_REVERSED.has(key)) return SEQ_REVERSED.get(key) as string;
  return v;
}

/** Deep copy of `option` with every colour-looking string resolved for the theme (functions kept as-is). */
export function themeOption<T>(option: T, tokens: Tokens, theme: ChartTheme): T {
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") return v.startsWith("var(") || v.startsWith("#") || v.startsWith("rgba(") ? resolveColor(v, tokens, theme) : v;
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object" && Object.getPrototypeOf(v) === Object.prototype) {
      const out: Record<string, unknown> = {};
      for (const [k, x] of Object.entries(v)) out[k] = walk(x);
      return out;
    }
    return v;
  };
  return walk(option) as T;
}
