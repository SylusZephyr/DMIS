"use client";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useSyncExternalStore } from "react";
import { MapPin } from "lucide-react";
import type { MarketRow } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

// The market the viewer is working on, shared by every page with a market context. Stored in this browser only
// (a per-viewer convenience); a ?market= query parameter or a /markets/<m> or /galaxy/<m> path selects it too.
const KEY = "dmis_market";
const EVT = "dmis-market";

function read(): string {
  try { return window.localStorage.getItem(KEY) ?? ""; } catch { return ""; }
}
function subscribe(cb: () => void) {
  window.addEventListener(EVT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(EVT, cb); window.removeEventListener("storage", cb); };
}

export function setGlobalMarket(m: string) {
  if (m === read()) return;
  try {
    if (m) window.localStorage.setItem(KEY, m); else window.localStorage.removeItem(KEY);
  } catch { /* storage unavailable: the choice lasts until the page reloads */ }
  window.dispatchEvent(new Event(EVT));
}

/** The selected market ("" = all / none) and its setter. */
export function useMarket(): [string, (m: string) => void] {
  const m = useSyncExternalStore(subscribe, read, () => "");
  return [m, setGlobalMarket];
}

/** Link targets that carry the selected market. `base` is a nav href. */
export function withMarket(base: string, market: string): string {
  if (!market) return base;
  const enc = encodeURIComponent(market);
  switch (base) {
    case "/markets": return `/markets/${enc}`;
    case "/galaxy": return `/galaxy/${enc}`;
    case "/competitors": case "/launch": case "/opportunities": return `${base}?market=${enc}`;
    default: return base;
  }
}

export function MarketUrlSync() {
  const path = usePathname();
  const q = useSearchParams().get("market");
  useEffect(() => {
    const m = path.match(/^\/(?:markets|galaxy)\/([^/]+)/);
    const fromUrl = q ?? (m ? decodeURIComponent(m[1]) : null);
    if (fromUrl) setGlobalMarket(fromUrl);
  }, [path, q]);
  return null;
}

function Picker({ compact }: { compact?: boolean }) {
  const mn = useMarketName();
  const { t } = useI18n();
  const [market, setMarket] = useMarket();
  const router = useRouter();
  const path = usePathname();
  const mk = useApi<MarketRow[]>("/markets");
  // a page showing one market follows the switcher: its path or ?market= parameter is rewritten
  const pick = (v: string) => {
    setMarket(v);
    const m = path.match(/^\/(markets|galaxy)\/[^/]+/);
    if (m) { router.push(v ? `/${m[1]}/${encodeURIComponent(v)}` : `/${m[1]}`); return; }
    const sp = new URLSearchParams(window.location.search);
    if (!sp.has("market")) return;
    if (v) sp.set("market", v); else sp.delete("market");
    router.push(sp.size ? `${path}?${sp}` : path);
  };
  const names = (mk.data ?? []).map((m) => m.name);
  // a remembered market that no longer exists (or is not visible to this user) is not offered
  const value = mk.data && market && !names.includes(market) ? "" : market;
  if (mk.error || (mk.data && names.length === 0)) return null;
  return (
    <label className={compact ? "flex min-w-0 items-center gap-1" : "block"}>
      {!compact && <span className="mb-1 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-3"><MapPin className="h-3 w-3" />{t("mkt.label")}</span>}
      <select aria-label={t("mkt.label")} value={value} onChange={(e) => pick(e.target.value)}
        className={`h-8 rounded-lg border border-line bg-panel-2 px-2 text-xs text-ink focus:border-accent focus:outline-none ${compact ? "w-32 min-w-0" : "w-full"}`}>
        <option value="">{t("mkt.all")}</option>
        {!mk.data && market && <option value={market}>{mn(market)}</option>}
        {names.map((n) => <option key={n} value={n}>{mn(n)}</option>)}
      </select>
    </label>
  );
}

/** Global market selector for the sidebar (and the compact mobile top bar). */
export function MarketSwitcher({ compact }: { compact?: boolean }) {
  return (
    <>
      <Suspense fallback={null}><MarketUrlSync /></Suspense>
      <Picker compact={compact} />
    </>
  );
}
