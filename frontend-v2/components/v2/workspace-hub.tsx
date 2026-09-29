"use client";
import Link from "next/link";
import { ArrowUpRight, Pin, PinOff } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import { useMarket } from "@/components/market-switcher";
import type { AlertRow, MarketRow } from "@/lib/api";
import { moneyShort, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { WORKSPACES, withMarket } from "@/lib/ia";
import { pinnedPages, togglePinned } from "@/lib/prefs";
import { cn } from "@/lib/utils";

type Overview = { markets: number; products: number; listings: number; suppliers: number; datasets: number; revenue_est: number | null; revenue_headline?: number | null; revenue_floor?: number | null;
  graph: { nodes: Record<string, number> } };

/** A workspace's landing page: what each of its tools is for, a live figure where one exists, and a way in. */
export function WorkspaceHub({ id, children }: { id: string; children?: React.ReactNode }) {
  const { t } = useI18n();
  const [market] = useMarket();
  const ws = WORKSPACES.find((w) => w.id === id)!;
  const pinned = pinnedPages.useValue();
  const ov = useApi<Overview>("/overview");
  const mk = useApi<MarketRow[]>("/markets");
  const alerts = useApi<AlertRow[]>(id === "execute" ? "/alerts" : null);
  const projects = useApi<{ projects: unknown[] }>(id === "execute" ? "/projects" : null);
  const o = ov.data;
  const best = Math.max(-1, ...(mk.data ?? []).map((m) => m.top_opportunity_score ?? -1));
  const stats: Record<string, string | null> = {
    "/markets": o ? t("hub.stat.markets", { n: num(o.markets), r: moneyShort(o.revenue_headline ?? o.revenue_floor ?? o.revenue_est) }) : null,
    "/search": o ? t("hub.stat.indexed", { n: num(o.products) }) : null,
    "/graph": o ? t("hub.stat.nodes", { n: num(Object.values(o.graph.nodes).reduce((a, b) => a + b, 0)) }) : null,
    "/galaxy": o ? t("hub.stat.products", { n: num(o.products) }) : null,
    "/intelligence": mk.data ? t("hub.stat.segments", { n: num(mk.data.reduce((a, m) => a + (m.segments ?? 0), 0)) }) : null,
    "/opportunities": best >= 0 ? t("hub.stat.best", { n: best.toFixed(0) }) : null,
    "/suppliers": o ? t("hub.stat.suppliers", { n: num(o.suppliers) }) : null,
    "/alerts": alerts.data ? t("hub.stat.unread", { n: alerts.data.filter((a) => a.status === "new").length }) : null,
    "/projects": projects.data ? t("hub.stat.projects", { n: projects.data.projects.length }) : null,
    "/data": o ? t("hub.stat.datasets", { n: num(o.datasets) }) : null,
  };
  return (
    <div className="space-y-5 px-4 pb-8 pt-6 md:px-6">
      <header className="flex items-center gap-4">
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-panel-2 text-accent glow"><ws.icon className="h-6 w-6" aria-hidden /></span>
        <div className="min-w-0">
          <h1 className="gradient-text text-2xl font-semibold tracking-tight">{t(ws.label)}</h1>
          <p className="text-sm text-ink-3">{t(ws.desc)}</p>
        </div>
      </header>
      <div className="stagger grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {ws.pages.map((p) => {
          const isPinned = pinned.includes(p.href);
          return (
            <Card key={p.href} className="lift group relative flex flex-col p-4 pb-12">
              <Link href={withMarket(p.href, market)} className="flex flex-1 flex-col after:absolute after:inset-0 after:content-['']">
                <span className="flex items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-line bg-panel-2 text-accent"><p.icon className="h-5 w-5" aria-hidden /></span>
                  <span className="min-w-0 flex-1 text-base font-semibold group-hover:text-accent">{t(p.label)}</span>
                  <ArrowUpRight className="h-4 w-4 text-ink-3 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent" aria-hidden />
                </span>
                <span className="mt-2 text-sm text-ink-3">{t(p.desc)}</span>
                {stats[p.href] && <span className="mt-3 inline-flex w-fit items-center gap-1.5 rounded-lg bg-accent/10 px-2 py-1 font-mono text-xs text-accent">{stats[p.href]}</span>}
              </Link>
              <button type="button" onClick={() => togglePinned(p.href)} aria-pressed={isPinned}
                aria-label={t(isPinned ? "ia.unpin" : "ia.pin")} title={t(isPinned ? "ia.unpin" : "ia.pin")}
                className={cn("absolute bottom-3 right-3 z-10 flex h-8 w-8 items-center justify-center rounded-lg border border-line text-ink-3 hover:text-accent",
                  isPinned ? "text-accent" : "opacity-70")}>
                {isPinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
              </button>
            </Card>
          );
        })}
      </div>
      {children}
    </div>
  );
}
