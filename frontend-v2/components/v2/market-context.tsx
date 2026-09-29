"use client";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, MapPin } from "lucide-react";
import { useMarket } from "@/components/market-switcher";
import { headlineOf } from "@/components/v2/market-size";
import type { MarketRow } from "@/lib/api";
import { moneyShort } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";
import { seq } from "@/lib/viz";

/** The global market context as a pill in the top bar. Choosing a market also moves a page that shows one
 *  market (its path or ?market=) to the new market, like the v1 switcher. */
export function MarketContext() {
  const { t } = useI18n();
  const mn = useMarketName();
  const [market, setMarket] = useMarket();
  const mk = useApi<MarketRow[]>("/markets");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  const names = (mk.data ?? []).map((m) => m.name);
  const current = names.includes(market) ? market : "";
  const pick = (v: string) => {
    setMarket(v); setOpen(false);
    const m = path.match(/^\/(markets|galaxy)\/[^/]+/);
    if (m) { router.push(v ? `/${m[1]}/${encodeURIComponent(v)}` : `/${m[1]}`); return; }
    const sp = new URLSearchParams(window.location.search);
    if (!sp.has("market")) return;
    if (v) sp.set("market", v); else sp.delete("market");
    router.push(sp.size ? `${path}?${sp}` : path);
  };
  if (mk.error || (mk.data && names.length === 0)) return null;
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open} aria-label={t("mkt.label")}
        className="flex h-9 max-w-[12rem] items-center gap-1.5 rounded-xl border border-line bg-panel-2/60 px-2.5 text-sm hover:border-accent/60">
        <MapPin className="h-4 w-4 shrink-0 text-accent" />
        <span className="truncate">{current ? mn(current) : t("mkt.all")}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-3" />
      </button>
      {open && (
        <div role="listbox" aria-label={t("mkt.label")} className="glass fade-up absolute right-0 top-11 z-50 w-72 overflow-hidden rounded-xl border border-line shadow-2xl">
          <div className="border-b border-line px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-3">{t("ia.marketContext")}</div>
          {[{ name: "" } as MarketRow, ...(mk.data ?? [])].map((m) => {
            const score = m.name ? m.top_opportunity_score ?? null : null;
            return (
              <button key={m.name || "all"} type="button" role="option" aria-selected={current === m.name} onClick={() => pick(m.name)}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-panel-2">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: m.name ? seq((score ?? 0) / 100) : "var(--line)" }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{m.name ? mn(m.name) : t("mkt.all")}</span>
                  {m.name && <span className="block text-[11px] text-ink-3">{moneyShort(headlineOf(m))}/mo · {t("ia.best")} {score?.toFixed(0) ?? "—"}</span>}
                </span>
                {current === m.name && <Check className="h-4 w-4 text-accent" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
