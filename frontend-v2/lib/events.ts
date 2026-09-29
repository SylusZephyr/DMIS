"use client";
import type { EventRow } from "@/lib/api";
import { pct, truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

/** One line describing a platform event (shared by the Alerts page and the notification bell). */
export function useDescribe() {
  const { t } = useI18n();
  return (e: EventRow) => {
    const p = e.payload as Record<string, number | string | undefined>;
    const n = (k: string) => Number(p[k]);
    switch (e.kind) {
      case "competitor.new_brand": return t("alerts.d.new_brand", { b: e.subject ?? "", s: pct(n("share"), 1), p: String(p.position ?? "") });
      case "competitor.price_change": return t("alerts.d.price", { b: e.subject ?? "", c: (n("change") * 100).toFixed(1) });
      case "competitor.share_change": return t("alerts.d.share", { b: e.subject ?? "", c: (n("change") * 100).toFixed(1) });
      case "competitor.position_change": return t("alerts.d.position", { b: e.subject ?? "", a: String(p.before), z: String(p.after) });
      case "opportunity.change": return t("alerts.d.opportunity", { s: e.subject ?? "", a: String(p.before), z: String(p.after) });
      case "opportunity.new_segment": return t("alerts.d.new_segment", { s: e.subject ?? "", v: String(p.opportunity_score) });
      case "trend.change": return t("alerts.d.trend", { a: String(p.before), z: String(p.after) });
      case "product.new_launch": return t("alerts.d.launch", { s: truncate(e.subject ?? "", 80) });
      case "segment.demand_change": return t("alerts.d.segment", { s: e.subject ?? "" });
      case "market.size_change": return t("alerts.d.size", { s: e.subject ?? "" });
      case "market.created": return t("alerts.d.market_created", { s: n("segments") || 0, b: n("brands") || 0 });
      case "dataset.processed": return t("alerts.d.dataset_processed", { f: e.subject ?? "" });
      default: return e.subject ?? e.kind;
    }
  };
}

