"use client";
import { useCallback } from "react";
import type { MarketRow } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

/** "denture_base" -> "Denture base" when the category has no configured name. */
export function humanize(slug: string): string {
  const s = slug.replace(/[_-]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : slug;
}

/** A function naming a market by its category's configured name (config/categories.yaml), in the viewer's
 *  language; the slug stays the identifier in URLs and the API. Shares the cached /markets query. */
export function useMarketName(): (slug: string | null | undefined) => string {
  const { lang } = useI18n();
  const { data } = useApi<MarketRow[]>("/markets");
  return useCallback((slug) => {
    if (!slug) return "";
    const m = data?.find((x) => x.name === slug);
    const name = (lang === "zh" ? m?.display_name_zh : null) ?? m?.display_name;
    return name ? name[0].toUpperCase() + name.slice(1) : humanize(slug);
  }, [data, lang]);
}
