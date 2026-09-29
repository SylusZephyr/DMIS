// Types and constants shared by the galaxy page and the 3D scene. Kept free of three.js imports so the
// page can use them without pulling three.js into its first-load bundle.
export type GalaxyProduct = {
  product_id: string; title: string; brand: string | null; segment_id: string; segment_label: string | null; family_label: string | null;
  image: string | null; price: number | null; gx: number; gy: number; gz: number; listing_count: number; model_label?: string | null;
  units_est?: number | null; units_lo?: number | null; units_hi?: number | null; revenue_est?: number | null; revenue_lo?: number | null;
  revenue_hi?: number | null; opportunity_score: number | null; margin_rate?: number | null; unit_margin?: number | null;
  is_entrant?: boolean | null; rating?: number | null; price_quartile?: number | null;
};
export type GalaxySegment = { segment_id: string; segment_label: string; family_label?: string; revenue_est?: number | null;
  opportunity_score?: number | null; opportunity_index?: number | null; listings?: number };
export type EdgeType = "similar" | "brand" | "segment";
export type GalaxyEdge = { source: string; target: string; type: Exclude<EdgeType, "segment">; score: number | null };
export const EDGE_COLOR: Record<EdgeType, string> = { similar: "#9085e9", brand: "#c98500", segment: "#5b6b82" };
