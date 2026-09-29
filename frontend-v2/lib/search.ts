import type { Schemas } from "@/lib/api-typed";

type Hit = Schemas["SearchHit"];

/** Where a global-search hit opens. */
export function hitHref(h: Hit): string {
  const m = encodeURIComponent(h.market ?? "");
  switch (h.kind) {
    case "product": return `/products/${encodeURIComponent(h.id)}`;
    case "segment": return `/intelligence?market=${m}&scope=segment&id=${encodeURIComponent(h.id)}`;
    case "taxonomy": return `/intelligence?market=${m}&scope=taxonomy&id=${encodeURIComponent(h.id)}`;
    case "brand": return `/competitors?market=${m}`;
    default: return `/suppliers/${encodeURIComponent(h.id)}`;
  }
}

