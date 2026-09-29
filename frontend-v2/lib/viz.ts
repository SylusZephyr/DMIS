// Chart colours validated for the dark panel surface (#0a1220) with the dataviz validator:
// categorical 8 hues -- worst adjacent CVD dE 8.4, normal-vision 19.3, all >= 3:1 contrast.
// Scatter / bubble forms: only the first 3 slots are all-pairs safe; more categories carry a legend,
// tooltip and click-to-isolate as secondary encoding, and anything past 7 folds into "Other".
export const CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
export const OTHER = "#5b6b82";
// sequential blue ramp (light -> dark on a dark surface we run dark -> light for "more")
export const SEQ = ["#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4"];
export const seq = (t: number) => SEQ[Math.max(0, Math.min(SEQ.length - 1, Math.round(t * (SEQ.length - 1))))];

/** Colour per category, stable by entity (sorted by size once, never by rank after filtering). */
export function categoricalMap(keys: string[], max = 7): Map<string, string> {
  const m = new Map<string, string>();
  keys.slice(0, max).forEach((k, i) => m.set(k, CATEGORICAL[i]));
  return m;
}
