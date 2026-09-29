"use client";
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { ArrowRight, CalendarDays, CheckCircle2, ChevronDown, Circle, LayoutGrid, ListChecks, ShieldCheck, Upload, X } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/primitives";
import type { MarketRow } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

// Per-viewer onboarding state (this browser only): dismissed flag and manually ticked steps.
const KEY = "dmis_onboarding";
const EVT = "dmis-onboarding";
type State = { dismissed?: boolean; done?: string[] };
function readRaw(): string {
  try { return window.localStorage.getItem(KEY) ?? "{}"; } catch { return "{}"; }
}
function parse(raw: string): State {
  try { return JSON.parse(raw) as State; } catch { return {}; }
}
function write(s: State) {
  try { window.localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage unavailable */ }
  window.dispatchEvent(new Event(EVT));
}
function subscribe(cb: () => void) {
  window.addEventListener(EVT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(EVT, cb); window.removeEventListener("storage", cb); };
}
function useOnboarding(): State {
  return parse(useSyncExternalStore(subscribe, readRaw, () => JSON.stringify({ dismissed: true })));
}

const STEPS = [
  { id: "upload", icon: Upload, href: "/data" },
  { id: "snapshot", icon: CalendarDays, href: "/data" },
  { id: "boundary", icon: ShieldCheck, href: "/data" },
  { id: "view", icon: LayoutGrid, href: "/markets" },
] as const;

/** First run: no market processed yet. Four steps from an export to a market analysis. */
export function FirstRunGuide() {
  const { t } = useI18n();
  return (
    <Card className="mx-6 mt-4">
      <CardHeader title={t("onb.firstTitle")} subtitle={t("onb.firstSub")} />
      <ol className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-4">
        {STEPS.map(({ id, icon: Icon, href }, i) => (
          <li key={id} className="flex flex-col rounded-lg border border-line bg-panel-2/40 p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/15 font-mono text-xs text-accent">{i + 1}</span>
              <Icon className="h-4 w-4 text-accent" />{t(`onb.step.${id}.title`)}
            </div>
            <p className="mt-2 flex-1 text-xs leading-relaxed text-ink-3">{t(`onb.step.${id}.body`)}</p>
            {i === 0 && (
              <Link href={href} className="mt-3 inline-flex items-center gap-1 self-start rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-[#04121c] hover:opacity-90">
                {t("onb.goUpload")} <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            )}
          </li>
        ))}
      </ol>
      <p className="px-4 pb-4 text-[11px] text-ink-3">{t("onb.firstNote")}</p>
    </Card>
  );
}

/** Dismissible "getting started" checklist once markets exist. Steps the data can confirm are ticked
 *  automatically; the rest are ticked by the viewer. */
export function GettingStarted({ markets, market }: { markets: MarketRow[]; market: string }) {
  const { t } = useI18n();
  const mn = useMarketName();
  const st = useOnboarding();
  const focus = markets.find((m) => m.name === market)?.name ?? markets[0]?.name ?? "";
  const snaps = useApi<{ declared: boolean }[]>(!st.dismissed && focus ? `/markets/${encodeURIComponent(focus)}/snapshots` : null, [focus]);
  const [expanded, setExpanded] = useState<boolean | null>(null);
  if (st.dismissed) return null;
  const enc = encodeURIComponent(focus);
  const auto: Record<string, boolean> = { upload: markets.length > 0, snapshot: !!snaps.data?.some((s) => s.declared) };
  const items = [
    { id: "upload", href: "/data" },
    { id: "snapshot", href: "/data" },
    { id: "boundary", href: `/markets/${enc}#scope` },
    { id: "view", href: `/markets/${enc}` },
    { id: "board", href: "/opportunities" },
    { id: "memo", href: "/opportunities" },
  ];
  const manual = new Set(st.done ?? []);
  const isDone = (id: string) => auto[id] || manual.has(id);
  const n = items.filter((i) => isDone(i.id)).length;
  const toggle = (id: string) => {
    const next = new Set(manual);
    if (next.has(id)) next.delete(id); else next.add(id);
    write({ ...st, done: [...next] });
  };
  // open until the first step is done; afterwards a one-line progress bar that expands on demand
  const open = expanded ?? n === 0;
  return (
    <Card className="mx-6 mt-4">
      <CardHeader title={<button type="button" onClick={() => setExpanded(!open)} aria-expanded={open}
          className="inline-flex min-h-6 items-center gap-1.5 hover:text-accent"><ListChecks className="h-3.5 w-3.5" />{t("onb.checkTitle", { n, m: items.length })}
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} /></button>}
        subtitle={open ? t("onb.checkSub", { market: mn(focus) }) : undefined}
        right={<button type="button" onClick={() => write({ ...st, dismissed: true })} aria-label={t("onb.dismiss")} title={t("onb.dismiss")}
          className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-1 text-xs text-ink-3 hover:bg-panel-2 hover:text-ink"><X className="h-3.5 w-3.5" />{t("onb.dismiss")}</button>} />
      <div className="h-1 bg-line"><div className="h-full bg-accent transition-all" style={{ width: `${(n / items.length) * 100}%` }} /></div>
      {open && <ul className="grid grid-cols-1 divide-y divide-line sm:grid-cols-2 sm:divide-y-0 xl:grid-cols-3">
        {items.map(({ id, href }) => {
          const done = isDone(id);
          return (
            <li key={id} className="flex items-start gap-2 px-4 py-2.5 text-sm">
              <button type="button" disabled={auto[id]} onClick={() => toggle(id)} aria-pressed={done}
                aria-label={t(done ? "onb.markUndone" : "onb.markDone")} className="mt-0.5 shrink-0 disabled:cursor-default">
                {done ? <CheckCircle2 className="h-4 w-4 text-good" /> : <Circle className="h-4 w-4 text-ink-3 hover:text-accent" />}
              </button>
              <div className="min-w-0">
                <Link href={href} className={done ? "text-ink-3 line-through decoration-ink-3/50" : "text-ink hover:text-accent"}>{t(`onb.check.${id}.title`)}</Link>
                <div className="text-[11px] text-ink-3">{t(`onb.check.${id}.body`)}</div>
              </div>
            </li>
          );
        })}
      </ul>}
    </Card>
  );
}
