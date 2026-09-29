"use client";
import Link from "next/link";
import { Tags } from "lucide-react";
import { num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Seg = { segment_id: string; searches: number; purchase_rate: number | null; gaps: number; top_keywords: string[] };
type Summary = { keywords: number; segments: Seg[] };

/** Search demand of one sub-category from the market's imported keyword export (nothing when none was imported). */
export function SearchDemand({ market, segmentId }: { market: string; segmentId: string }) {
  const { t } = useI18n();
  const s = useApi<Summary>(`/markets/${encodeURIComponent(market)}/keywords/summary`, [market]);
  if (!s.data?.keywords) return null;
  const g = s.data.segments.find((x) => x.segment_id === segmentId);
  const href = `/keywords?${new URLSearchParams({ market, segment: segmentId })}`;
  if (!g) return <div className="text-[11px] text-ink-3"><Tags className="mr-1 inline h-3 w-3" aria-hidden />{t("kw.noneForSeg")}</div>;
  return (
    <div className="space-y-0.5">
      <div><Tags className="mr-1 inline h-3.5 w-3.5 text-ink-3" aria-hidden />{t("kw.segDemand")}: <span className="font-mono">{num(g.searches)}</span>{t("kw.perMonth")}
        {g.purchase_rate != null && <span className="text-ink-3"> · {t("kw.rate", { r: pct(g.purchase_rate, 1) })}</span>}
        {g.gaps > 0 && <span className="text-good"> · {t("kw.gapsN", { n: g.gaps })}</span>}</div>
      <div className="truncate text-[11px] text-ink-3" title={g.top_keywords.join(", ")}>{g.top_keywords.slice(0, 3).join(" · ")} <Link href={href} className="text-accent hover:underline">→</Link></div>
    </div>
  );
}
