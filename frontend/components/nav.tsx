"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { Bell, BookOpen, ChevronDown, Search, ListChecks, Map as MapIcon, Upload, Boxes, Building2, ClipboardCheck, Target, ClipboardList, Columns2, Compass, Gauge, FolderKanban, Inbox, Database, Factory, Globe2, Hammer, LayoutGrid, Lightbulb, MessageSquareText, Network, Rocket, ShieldCheck, ShoppingBag, Menu, Swords, Trophy, UserRound, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { WhoAmI } from "@/components/auth-gate";
import { MarketSwitcher, useMarket, withMarket } from "@/components/market-switcher";
import { LangSwitch, useI18n } from "@/lib/i18n";
import { ThemeToggle } from "@/components/theme-toggle";

// Four workspaces: explore the market, decide what to sell, execute the launch, govern the data and the platform.
const GROUPS = [
  { title: "explore", icon: Compass, items: [
    { href: "/", label: "command", icon: Globe2 },
    { href: "/search", label: "search", icon: Search },
    { href: "/intelligence", label: "intelMap", icon: MapIcon },
    { href: "/markets", label: "markets", icon: LayoutGrid },
    { href: "/graph", label: "graph", icon: Network },
  ]},
  { title: "decide", icon: Lightbulb, items: [
    { href: "/opportunities", label: "opportunities", icon: Trophy },
    { href: "/compare", label: "compare", icon: Columns2 },
    { href: "/analyst", label: "analyst", icon: MessageSquareText },
    { href: "/launch", label: "launch", icon: Rocket },
    { href: "/competitors", label: "competitors", icon: Swords },
    { href: "/alerts", label: "alerts", icon: Bell },
    { href: "/shop", label: "shop", icon: ShoppingBag },
  ]},
  { title: "execute", icon: Hammer, items: [
    { href: "/projects", label: "projects", icon: FolderKanban },
    { href: "/inbox", label: "inbox", icon: Inbox },
    { href: "/suppliers", label: "suppliers", icon: Factory },
    { href: "/portfolio", label: "portfolio", icon: UserRound },
  ]},
  { title: "govern", icon: ShieldCheck, items: [
    { href: "/import", label: "importData", icon: Upload },
    { href: "/review", label: "review", icon: ListChecks },
    { href: "/data", label: "data", icon: Database },
    { href: "/accuracy", label: "accuracy", icon: Target },
    { href: "/labelling", label: "labelling", icon: ClipboardCheck },
    { href: "/pilot", label: "pilotPage", icon: Gauge },
    { href: "/audit", label: "audit", icon: ClipboardList },
    { href: "/org", label: "org", icon: Building2 },
    { href: "/methodology", label: "methodology", icon: BookOpen },
  ]},
];

// Which groups are collapsed: a per-viewer convenience kept in this browser. The group holding the current page
// is always open, so a collapsed group never hides where you are.
const NAV_KEY = "dmis_nav_collapsed";
const NAV_EVT = "dmis-nav";
const NAV_DEFAULT = "[\"govern\"]";
let navMem: string | null = null;           // used when browser storage is unavailable
function readCollapsed(): string {
  try { return window.localStorage.getItem(NAV_KEY) ?? navMem ?? NAV_DEFAULT; } catch { return navMem ?? NAV_DEFAULT; }
}
function writeCollapsed(v: string[]) {
  navMem = JSON.stringify(v);
  try { window.localStorage.setItem(NAV_KEY, navMem); } catch { /* storage unavailable: kept in memory */ }
  window.dispatchEvent(new Event(NAV_EVT));
}
function subscribeCollapsed(cb: () => void) {
  window.addEventListener(NAV_EVT, cb);
  return () => window.removeEventListener(NAV_EVT, cb);
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const { t } = useI18n();
  const [market] = useMarket();
  const raw = useSyncExternalStore(subscribeCollapsed, readCollapsed, () => NAV_DEFAULT);
  const collapsed: string[] = (() => { try { return JSON.parse(raw); } catch { return ["govern"]; } })();
  const toggle = (g: string) => {
    const next = collapsed.includes(g) ? collapsed.filter((x) => x !== g) : [...collapsed, g];
    writeCollapsed(next);
  };
  const isActive = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));
  return (
    <nav className="flex-1 space-y-3 overflow-y-auto px-3 pb-3 scrollbar-thin">
      {GROUPS.map((g) => {
        const here = g.items.some((i) => isActive(i.href));
        const open = here || !collapsed.includes(g.title);
        return (
          <div key={g.title}>
            <button type="button" onClick={() => toggle(g.title)} aria-expanded={open} disabled={here}
              className="flex min-h-7 w-full items-center gap-1.5 rounded px-2 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-3 hover:text-ink disabled:hover:text-ink-3">
              <g.icon className="h-3 w-3" /><span className="flex-1 text-left">{t(`nav.${g.title}`)}</span>
              {!here && <ChevronDown className={cn("h-3 w-3 transition-transform", open ? "" : "-rotate-90")} />}
            </button>
            {open && g.items.map(({ href, label, icon: Icon }) => {
              const active = isActive(href);
              return (
                <Link key={href} href={withMarket(href, market)} onClick={onNavigate} aria-current={active ? "page" : undefined}
                  className={cn("flex min-h-8 items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm transition-colors",
                    active ? "bg-accent/10 text-accent" : "text-ink-2 hover:bg-panel-2 hover:text-ink")}>
                  <Icon className="h-4 w-4" /> {t(`nav.${label}`)}
                </Link>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2">
      <Boxes className="h-5 w-5 text-accent" />
      <div>
        <div className="text-sm font-bold tracking-wide">DMIS</div>
        <div className="text-[10px] uppercase tracking-[0.18em] text-ink-3">Intelligence OS</div>
      </div>
    </Link>
  );
}

export function Nav() {
  return (
    <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-nav md:flex">
      <div className="px-5 pb-3 pt-5"><Brand /></div>
      <div className="px-5 pb-3"><MarketSwitcher /></div>
      <NavLinks />
      <div className="space-y-1 border-t border-line px-5 py-3 text-[10px] text-ink-3">
        <div className="flex items-center gap-2"><LangSwitch /><ThemeToggle /></div>
        <WhoAmI />
        <div>Platform v2 · Core v1 inside</div>
      </div>
    </aside>
  );
}

/** Phones and small tablets: a top bar with the language switch and a menu that slides in the same links. */
export function MobileNav() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <div className="md:hidden">
      <div className="sticky top-0 z-40 flex items-center justify-between border-b border-line bg-nav/95 px-4 py-2.5 backdrop-blur">
        <Brand />
        <div className="flex items-center gap-3 text-[10px]">
          <LangSwitch />
          <ThemeToggle />
          <button aria-label={t("nav.menu")} onClick={() => setOpen(true)} className="rounded-lg border border-line p-1.5 text-ink-2"><Menu className="h-4 w-4" /></button>
        </div>
      </div>
      {open && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex h-full w-72 max-w-[85vw] flex-col border-r border-line bg-nav">
            <div className="flex items-center justify-between px-5 py-4"><Brand />
              <button aria-label={t("nav.close")} onClick={() => setOpen(false)} className="text-ink-3"><X className="h-5 w-5" /></button></div>
            <div className="px-5 pb-3"><MarketSwitcher /></div>
            <NavLinks onNavigate={() => setOpen(false)} />
            <div className="border-t border-line px-5 py-3 text-[10px] text-ink-3"><WhoAmI /></div>
          </div>
          <button aria-label={t("nav.close")} className="flex-1 bg-black/50" onClick={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
