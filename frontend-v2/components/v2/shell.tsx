"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Boxes, ChevronRight, Command, Menu, PanelLeftClose, PanelLeftOpen, Pin, PinOff, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n, LangSwitch } from "@/lib/i18n";
import { ThemeToggle } from "@/components/theme-toggle";
import { WhoAmI } from "@/components/auth-gate";
import { MarketSwitcher, MarketUrlSync, useMarket } from "@/components/market-switcher";
import { useMarketName } from "@/lib/market-name";
import { ALL_PAGES, WORKSPACES, locate, withMarket, type Workspace } from "@/lib/ia";
import { panelCollapsed, pinnedPages, pushRecent, togglePinned } from "@/lib/prefs";
import { CommandPalette, openPalette } from "@/components/v2/command-palette";
import { Notifications } from "@/components/v2/notifications";
import { MarketContext } from "@/components/v2/market-context";

function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2" aria-label="DMIS">
      <span className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-panel-2">
        <Boxes className="h-5 w-5 text-accent" />
        <span aria-hidden className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-good" />
      </span>
      {!compact && <span className="leading-tight"><span className="block text-sm font-bold tracking-wide">DMIS</span>
        <span className="block text-[10px] uppercase tracking-[0.18em] text-ink-3">Intelligence OS · v2</span></span>}
    </Link>
  );
}

/** Left rail: one icon per workspace. */
function Rail({ current }: { current: Workspace }) {
  const { t } = useI18n();
  return (
    <nav aria-label={t("ia.workspaces")} className="flex flex-col items-center gap-1.5 py-3">
      {WORKSPACES.map((w) => {
        const active = w.id === current.id;
        return (
          <Link key={w.id} href={w.href} aria-current={active ? "page" : undefined} title={t(w.label)}
            className={cn("group relative flex h-11 w-11 flex-col items-center justify-center rounded-xl text-ink-3 transition-colors",
              active ? "bg-accent/12 text-accent" : "hover:bg-panel-2 hover:text-ink")}>
            {active && <span aria-hidden className="accent-bar absolute -left-3 h-6 w-1 rounded-r-full" />}
            <w.icon className="h-5 w-5" />
            <span className="mt-0.5 text-[9px] font-medium leading-none">{t(w.label)}</span>
          </Link>
        );
      })}
    </nav>
  );
}

/** Section panel: the pages of the current workspace, plus pinned pages. */
function Panel({ current, path, onNavigate }: { current: Workspace; path: string; onNavigate?: () => void }) {
  const { t } = useI18n();
  const [market] = useMarket();
  const pinned = pinnedPages.useValue();
  const pins = pinned.map((h) => ALL_PAGES.find((p) => p.href === h)).filter((p): p is (typeof ALL_PAGES)[number] => !!p);
  const isActive = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));
  const item = (p: (typeof ALL_PAGES)[number] | Workspace["pages"][number]) => {
    const active = isActive(p.href);
    return (
      <div key={p.href} className="group/item relative">
        <Link href={withMarket(p.href, market)} onClick={onNavigate} aria-current={active ? "page" : undefined}
          className={cn("flex min-h-9 items-center gap-2.5 rounded-lg px-2.5 py-1.5 pr-8 text-sm transition-colors",
            active ? "bg-accent/10 text-accent" : "text-ink-2 hover:bg-panel-2 hover:text-ink")}>
          <p.icon className="h-4 w-4 shrink-0" /><span className="truncate">{t(p.label)}</span>
        </Link>
        <button type="button" onClick={() => togglePinned(p.href)} aria-label={t(pinned.includes(p.href) ? "ia.unpin" : "ia.pin")}
          title={t(pinned.includes(p.href) ? "ia.unpin" : "ia.pin")}
          className="absolute right-1 top-1/2 hidden h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-ink-3 hover:text-accent focus-visible:flex group-hover/item:flex">
          {pinned.includes(p.href) ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
        </button>
      </div>
    );
  };
  return (
    <div className="flex h-full flex-col">
      <div className="px-4 pb-2 pt-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-3">{t("ia.workspace")}</div>
        <Link href={current.href} onClick={onNavigate} className="mt-0.5 block text-base font-semibold hover:text-accent">{t(current.label)}</Link>
        <p className="mt-0.5 text-[11px] leading-snug text-ink-3">{t(current.desc)}</p>
      </div>
      <nav aria-label={t(current.label)} className="scrollbar-thin flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {current.pages.filter((p) => !p.hidden).map(item)}
        {pins.length > 0 && <>
          <div className="px-2.5 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-3">{t("ia.pinned")}</div>
          {pins.map(item)}
        </>}
      </nav>
      <div className="space-y-2 border-t border-line px-4 py-3 text-[10px] text-ink-3">
        <WhoAmI />
        <div>{t("ia.version")}</div>
      </div>
    </div>
  );
}

function Breadcrumbs({ path }: { path: string }) {
  const { t } = useI18n();
  const mn = useMarketName();
  const { workspace, page } = locate(path);
  const crumbs: { label: string; href?: string }[] = [];
  if (workspace.id !== "home" || page?.href !== "/") crumbs.push({ label: t(workspace.label), href: workspace.href });
  if (page && page.href !== workspace.href) crumbs.push({ label: t(page.label), href: page.href });
  // an entity below the page (a market, a product, a supplier)
  const rest = page && page.href !== "/" ? path.slice(page.href.length).split("/").filter(Boolean) : [];
  if (rest.length) {
    const first = decodeURIComponent(rest[0]);
    const label = ["/markets", "/galaxy"].includes(page!.href) ? mn(first) : first.length > 18 ? `${first.slice(0, 16)}…` : first;
    crumbs.push({ label, href: rest.length > 1 ? `${page!.href}/${rest[0]}` : undefined });
    if (rest[1] === "hierarchy") crumbs.push({ label: t("hier.title") });
  }
  if (!crumbs.length) crumbs.push({ label: t("ia.missionControl") });
  return (
    <nav aria-label={t("ia.breadcrumb")} className="flex min-w-0 items-center gap-1 text-sm">
      {crumbs.map((c, i) => (
        <span key={i} className="flex min-w-0 items-center gap-1">
          {i > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-3" />}
          {c.href && i < crumbs.length - 1
            ? <Link href={c.href} className="truncate text-ink-3 hover:text-accent">{c.label}</Link>
            : <span className="truncate font-medium text-ink" aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>}
        </span>
      ))}
    </nav>
  );
}

/** Keyboard: ⌘K / Ctrl+K or "/" opens the palette; "g" then a letter jumps to a workspace. */
function Shortcuts() {
  const router = useRouter();
  useEffect(() => {
    let g = 0;
    const jump: Record<string, string> = { h: "/", d: "/discover", c: "/decide", e: "/execute", q: "/quality", a: "/admin",
      o: "/opportunities", m: "/markets", s: "/search", p: "/projects" };
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); openPalette(); return; }
      if (e.key === "g") { g = Date.now(); return; }
      if (Date.now() - g < 900 && jump[e.key]) { e.preventDefault(); g = 0; router.push(jump[e.key]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);
  return null;
}

function RecentTracker({ path }: { path: string }) {
  useEffect(() => { pushRecent(path); }, [path]);
  return null;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { t } = useI18n();
  const { workspace } = locate(path);
  const collapsed = panelCollapsed.useValue();
  const [drawer, setDrawer] = useState(false);
  return (
    <div className="aurora flex min-h-screen w-full">
      <Shortcuts />
      <RecentTracker path={path} />
      <Suspense fallback={null}><MarketUrlSync /></Suspense>
      {/* desktop: rail + section panel */}
      <aside className="sticky top-0 hidden h-screen shrink-0 border-r border-line bg-nav/80 backdrop-blur md:flex">
        <div className="flex w-[76px] flex-col items-center border-r border-line">
          <div className="pb-1 pt-4"><Brand compact /></div>
          <Rail current={workspace} />
          <div className="mt-auto flex flex-col items-center gap-2 pb-4">
            <button type="button" onClick={() => panelCollapsed.set(!collapsed)} aria-label={t(collapsed ? "ia.expandPanel" : "ia.collapsePanel")}
              title={t(collapsed ? "ia.expandPanel" : "ia.collapsePanel")} className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-3 hover:bg-panel-2 hover:text-ink">
              {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
          </div>
        </div>
        {!collapsed && <div className="w-60"><Panel current={workspace} path={path} /></div>}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-bg/70 px-4 backdrop-blur-xl md:px-6">
          <button type="button" className="flex h-9 w-9 items-center justify-center rounded-lg border border-line md:hidden" aria-label={t("nav.menu")} onClick={() => setDrawer(true)}>
            <Menu className="h-4 w-4" /></button>
          <div className="md:hidden"><Brand compact /></div>
          <div className="hidden min-w-0 flex-1 md:block"><Breadcrumbs path={path} /></div>
          <div className="flex-1 md:hidden" />
          <button type="button" onClick={openPalette} aria-label={t("ia.searchEverything")}
            className="group hidden h-9 w-72 items-center gap-2 rounded-xl border border-line bg-panel-2/60 px-3 text-sm text-ink-3 hover:border-accent/60 hover:text-ink lg:flex">
            <Search className="h-4 w-4" /><span className="flex-1 text-left">{t("ia.searchEverything")}</span>
            <kbd className="key"><Command className="h-2.5 w-2.5" /></kbd><kbd className="key">K</kbd>
          </button>
          <button type="button" onClick={openPalette} aria-label={t("ia.searchEverything")}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-line lg:hidden"><Search className="h-4 w-4" /></button>
          <MarketContext />
          <Notifications />
          <div className="hidden items-center gap-2 sm:flex"><LangSwitch /><ThemeToggle /></div>
        </header>
        <main id="main" tabIndex={-1} className="relative min-w-0 flex-1 overflow-x-clip pb-24 focus:outline-none md:pb-16">
          <div key={path} className="fade-up">{children}</div>
        </main>
      </div>

      {/* phones: bottom workspace bar and a drawer with the section panel */}
      <nav aria-label={t("ia.workspaces")} className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-6 border-t border-line bg-nav/95 backdrop-blur md:hidden">
        {WORKSPACES.map((w) => (
          <Link key={w.id} href={w.href} aria-current={w.id === workspace.id ? "page" : undefined}
            className={cn("flex min-h-14 flex-col items-center justify-center gap-0.5 text-[10px]", w.id === workspace.id ? "text-accent" : "text-ink-3")}>
            <w.icon className="h-5 w-5" />{t(w.label)}</Link>
        ))}
      </nav>
      {drawer && (
        <div className="fixed inset-0 z-50 flex md:hidden" role="dialog" aria-modal="true" aria-label={t("nav.menu")}>
          <div className="flex h-full w-80 max-w-[88vw] flex-col border-r border-line bg-nav">
            <div className="flex items-center justify-between px-4 py-3"><Brand />
              <button type="button" aria-label={t("nav.close")} onClick={() => setDrawer(false)} className="flex h-9 w-9 items-center justify-center text-ink-3"><X className="h-5 w-5" /></button></div>
            <div className="flex items-center gap-2 px-4 pb-2"><LangSwitch /><ThemeToggle /></div>
            <div className="px-4 pb-2"><MarketSwitcher /></div>
            <div className="min-h-0 flex-1"><Panel current={workspace} path={path} onNavigate={() => setDrawer(false)} /></div>
          </div>
          <button type="button" aria-label={t("nav.close")} className="flex-1 bg-scrim" onClick={() => setDrawer(false)} />
        </div>
      )}
      <CommandPalette />
    </div>
  );
}
