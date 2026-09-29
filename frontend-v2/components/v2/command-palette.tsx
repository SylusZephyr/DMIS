"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ArrowRight, Clock, CornerDownLeft, MapPin, Pin, Search, Sparkles, type LucideIcon } from "lucide-react";
import { setGlobalMarket, useMarket } from "@/components/market-switcher";
import type { MarketRow } from "@/lib/api";
import type { Schemas } from "@/lib/api-typed";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { nodeLabel, useMarketName } from "@/lib/market-name";
import { ALL_PAGES, withMarket } from "@/lib/ia";
import { pinnedPages, recentPages } from "@/lib/prefs";
import { hitHref } from "@/lib/search";
import { cn } from "@/lib/utils";

// open/close from anywhere (top bar button, keyboard shortcut)
const EVT = "dmis-palette";
let isOpen = false;
function subscribe(cb: () => void) { window.addEventListener(EVT, cb); return () => window.removeEventListener(EVT, cb); }
export function openPalette() { isOpen = true; window.dispatchEvent(new Event(EVT)); }
function closePalette() { isOpen = false; window.dispatchEvent(new Event(EVT)); }

type Item = { id: string; group: string; label: string; sub?: string; icon: LucideIcon; run: () => void; score: number };

/** Case- and accent-insensitive subsequence score: contiguous and early matches rank higher; 0 = no match. */
function fuzzy(q: string, text: string): number {
  if (!q) return 1;
  const a = q.toLowerCase(), b = text.toLowerCase();
  const i = b.indexOf(a);
  if (i >= 0) return 100 - Math.min(i, 50) + (i === 0 ? 20 : 0);
  let j = 0, run = 0, best = 0;
  for (const ch of b) {
    if (ch === a[j]) { j++; run++; best = Math.max(best, run); if (j === a.length) return 10 + best; } else run = 0;
  }
  return 0;
}

function useDebounced(v: string, ms: number) {
  const [d, setD] = useState(v);
  useEffect(() => { const h = setTimeout(() => setD(v), ms); return () => clearTimeout(h); }, [v, ms]);
  return d;
}

export function CommandPalette() {
  const open = useSyncExternalStore(subscribe, () => isOpen, () => false);
  return open ? <Palette /> : null;
}

function Palette() {
  const { t } = useI18n();
  const mn = useMarketName();
  const router = useRouter();
  const [market] = useMarket();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const mk = useApi<MarketRow[]>("/markets");
  const dq = useDebounced(q.trim(), 200);
  const search = useApi<Schemas["SearchResponse"]>(dq.length >= 2 ? `/search?q=${encodeURIComponent(dq)}&limit=5` : null, [dq]);
  const recent = recentPages.useValue();
  const pinned = pinnedPages.useValue();

  useEffect(() => { input.current?.focus(); }, []);

  const go = (href: string) => { closePalette(); router.push(href); };
  const items = useMemo<Item[]>(() => {
    const s = q.trim();
    const out: Item[] = [];
    const page = (p: (typeof ALL_PAGES)[number], group: string, bonus = 0) => {
      const text = `${t(p.label)} ${t(p.workspace.label)} ${p.keywords ?? ""} ${t(p.desc)}`;
      const sc = fuzzy(s, `${t(p.label)}`) * 2 || fuzzy(s, text);
      if (sc) out.push({ id: `${group}:${p.href}`, group, label: t(p.label), sub: `${t(p.workspace.label)} · ${t(p.desc)}`, icon: p.icon,
        run: () => go(withMarket(p.href, market)), score: sc + bonus });
    };
    if (!s) {
      for (const h of pinned) { const p = ALL_PAGES.find((x) => x.href === h); if (p) page(p, "ia.pinned", 1000); }
      for (const h of recent.slice(0, 5)) {
        const p = ALL_PAGES.find((x) => x.href === h);
        if (p && !pinned.includes(h)) page(p, "ia.recent", 500);
        else if (!p && h !== "/") out.push({ id: `recent:${h}`, group: "ia.recent", label: decodeURIComponent(h), icon: Clock, run: () => go(h), score: 400 });
      }
    }
    for (const p of ALL_PAGES) if (!out.some((o) => o.id.endsWith(`:${p.href}`))) page(p, "ia.pages");
    for (const m of mk.data ?? []) {
      const name = mn(m.name);
      const sc = fuzzy(s, `${name} ${m.name} ${m.display_name_zh ?? ""}`);
      if (sc) {
        out.push({ id: `mkt:${m.name}`, group: "ia.markets", label: name, sub: t("ia.openMarket"), icon: MapPin, run: () => { setGlobalMarket(m.name); go(`/markets/${encodeURIComponent(m.name)}`); }, score: sc });
        if (m.name !== market) out.push({ id: `ctx:${m.name}`, group: "ia.actions", label: t("ia.switchTo", { m: name }), icon: Sparkles, run: () => { setGlobalMarket(m.name); closePalette(); }, score: sc - 1 });
      }
    }
    const actions: [string, string][] = [["ia.act.upload", "/data"], ["ia.act.simulate", "/launch"], ["ia.act.ask", "/analyst"],
      ["ia.act.compare", "/compare?mode=concepts"], ["ia.act.project", "/projects"], ["ia.act.review", "/review"]];
    for (const [k, href] of actions) { const sc = fuzzy(s, t(k)); if (sc) out.push({ id: `act:${k}`, group: "ia.actions", label: t(k), icon: ArrowRight, run: () => go(href), score: sc }); }
    for (const [kind, hits] of Object.entries(search.data?.hits ?? {})) {
      for (const h of (hits ?? []).slice(0, 4)) {
        out.push({ id: `hit:${kind}:${h.id}`, group: `search.kind.${kind}`, label: kind === "taxonomy" ? nodeLabel(h.label) : h.label, sub: [h.market ? mn(h.market) : null, h.detail].filter(Boolean).join(" · "),
          icon: Search, run: () => go(hitHref(h)), score: 50 * (h.score ?? 0) });
      }
    }
    const order = ["ia.pinned", "ia.recent", "ia.pages", "ia.markets", "ia.actions"];
    const rank = (g: string) => { const i = order.indexOf(g); return i < 0 ? order.length : i; };
    return out.filter((o) => o.score > 0)
      .sort((a, b) => (s ? b.score - a.score : rank(a.group) - rank(b.group) || b.score - a.score))
      .slice(0, 40);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, t, mn, mk.data, search.data, recent, pinned, market]);

  // keep the selection on screen
  useEffect(() => { listRef.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" }); }, [sel]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setSel((i) => Math.min(items.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); items[sel]?.run(); }
  };
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-scrim/70 p-4 pt-[12vh] backdrop-blur-sm" onMouseDown={closePalette}>
      <div role="dialog" aria-modal="true" aria-label={t("ia.palette")} onMouseDown={(e) => e.stopPropagation()}
        className="glass fade-up w-full max-w-2xl overflow-hidden rounded-2xl border border-line shadow-2xl">
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Search className="h-5 w-5 text-accent" />
          <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }} onKeyDown={onKey}
            role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={items[sel] ? `pi-${sel}` : undefined}
            aria-label={t("ia.searchEverything")} placeholder={t("ia.palettePh")}
            className="h-14 flex-1 bg-transparent text-base text-ink placeholder:text-ink-3 focus:outline-none focus-visible:outline-none" />
          {search.loading && <span className="live-dot h-2 w-2 rounded-full text-accent" aria-hidden />}
          <kbd className="key">Esc</kbd>
        </div>
        <ul id="palette-list" ref={listRef} role="listbox" className="scrollbar-thin max-h-[55vh] overflow-y-auto p-2">
          {items.length === 0 && <li className="px-3 py-8 text-center text-sm text-ink-3">{t("ia.noResults")}</li>}
          {items.map((it, i) => {
            const header = i === 0 || items[i - 1].group !== it.group ? it.group : null;
            return (
              <li key={it.id} role="presentation">
                {header && <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-3">{t(header)}</div>}
                <div id={`pi-${i}`} data-i={i} role="option" aria-selected={i === sel} onMouseMove={() => setSel(i)} onClick={() => it.run()}
                  className={cn("flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2", i === sel ? "bg-accent/12 text-ink" : "text-ink-2")}>
                  <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line", i === sel ? "text-accent" : "text-ink-3")}>
                    {it.group === "ia.pinned" ? <Pin className="h-4 w-4" /> : <it.icon className="h-4 w-4" />}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{it.label}</span>
                    {it.sub && <span className="block truncate text-[11px] text-ink-3">{it.sub}</span>}
                  </span>
                  {i === sel && <CornerDownLeft className="h-4 w-4 text-ink-3" />}
                </div>
              </li>
            );
          })}
        </ul>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-[11px] text-ink-3">
          <span><kbd className="key">↑</kbd> <kbd className="key">↓</kbd> {t("ia.kNavigate")}</span>
          <span><kbd className="key">↵</kbd> {t("ia.kOpen")}</span>
          <span><kbd className="key">G</kbd> + <kbd className="key">O</kbd> {t("ia.kJump")}</span>
        </div>
      </div>
    </div>
  );
}
